"""Sandboxed run_python tool: source guards, CMD sanity guard, retry fixes.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: run_python.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import asyncio
import math
import re
import time
from typing import Any

from app.services.ai_tools import _CACHE_TTL_SECONDS, get_cached_results, logger

TOOL_SCHEMAS = [
    {
        "name": "run_python",
        "description": (
            "Execute Python code for data analysis, statistical modeling, or visualization "
            "only when a trusted local operator has explicitly enabled a legacy executor. "
            "Hosted production disables this tool until an external OS-isolated, no-secrets "
            "runner exists. Prefer typed analysis tools whenever possible. "
            "Available libraries: numpy (as np), scipy, astropy (Table, SkyCoord, units as u), "
            "matplotlib.pyplot (as plt), pandas. "
            "The Standard Astro helper toolkit is preloaded as `astro` and may also be imported as `astro`. "
            "Helper functions: load_fits(path) returns astropy HDUList, "
            "get_search_results() returns the latest search results as a list of dicts, "
            "get_adql_results() returns only the latest ADQL rows as list[dict], "
            "get_adql_result_sets() returns recent ADQL result sets with query/service metadata, "
            "astro.search_lightcurve(...) returns list[dict]; access the first row as results[0]['mission'] "
            "or results[0].get('sector'). astro.download_and_clean_lightcurve(...) returns a dict with "
            "keys ['time', 'flux', 'flux_err', 'meta']. astro.phase_fold(time, flux, period, t0) returns "
            "a PhaseFoldResult supporting `phase, flux_folded = result`, `result.phase`, and result['phase']. "
            "load_votable(path) loads a VOTable as an astropy Table, "
            "load_csv(path) loads a CSV as a pandas DataFrame, "
            "process_in_chunks(data, chunk_size, func) processes large data in memory-safe chunks, "
            "memory_usage_mb() returns current memory usage in MB. "
            "Use print() to output results. Matplotlib figures are automatically captured. "
            "Max execution time: 75s (normal), 30s (fast), 300s (slow — declare mode='slow' "
            "for BLS / MCMC / large bootstrap / grid searches). "
            "REQUIRED: You MUST declare `data_source` — where the code's input data comes from. "
            "If you are NOT analyzing real observational data (e.g. demonstrating a formula, "
            "generating a plot template, listing helper functions with available_functions(), "
            "or checking helper signatures before a real analysis), declare "
            "`data_source=\"none_not_analyzing_real_data\"`. The system will mark the output as "
            "SYNTHETIC and the zero-fabrication gate will block using its facts, numbers, "
            "or conclusions in a real-data answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute. Use print() for output and plt for plots.",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the code does",
                },
                "data_source": {
                    "type": "string",
                    "description": (
                        "G1: where this code's data comes from. REQUIRED. One of: "
                        "'latest_adql' (uses get_adql_results / get_adql_result_sets), "
                        "'latest_search' (uses get_search_results), "
                        "'latest_lightcurve' / 'cached:<key>' (other cached real data), "
                        "'latest_high_velocity_stars' (uses the focused Gaia high-velocity cache), "
                        "'fits:<path>' (loads a specific FITS file via load_fits), "
                        "'user_file:<path>' (your own uploaded data file read via "
                        "pd.read_csv / pd.read_parquet / load_csv — real data, not synthetic), "
                        "'none_not_analyzing_real_data' (not analyzing observational data: "
                        "synthetic/pedagogical/Monte-Carlo data, or helper introspection such as "
                        "available_functions(); output will be marked SYNTHETIC/not citable for "
                        "scientific facts, numbers, or conclusions). "
                        "If a real data-fetch tool FAILED earlier this turn, choosing "
                        "'none_not_analyzing_real_data' to replace it is forbidden — emit "
                        "<tools_returned_nothing/> instead."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["fast", "normal", "slow"],
                    "description": (
                        "G5: execution-time budget. `fast` = 30s (small string / table "
                        "manipulation), `normal` = 75s (default), `slow` = 300s (BLS, MCMC, "
                        "bootstrap, PARSEC grid, large cross-matches). Default normal if omitted."
                    ),
                },
            },
            "required": ["code", "data_source"],
        },
    },
]


_VALID_DATA_SOURCES = {
    "latest_adql", "latest_search", "latest_lightcurve",
    "latest_sdss_sql",  # J3: SDSS SkyServer direct-connection results
    "latest_high_velocity_stars",
    "none_not_analyzing_real_data",
}
_REAL_DATA_SOURCE_PATTERNS = {
    # G1.2: for each declared data_source, these substrings must appear in
    # the code — else the declaration is inconsistent with the code and
    # we reject it.
    "latest_adql": ("get_adql_results", "get_adql_result_sets", "get_cached_results"),
    "latest_search": ("get_search_results", "get_cached_results"),
    "latest_lightcurve": ("get_cached_results", "lightkurve", "search_lightcurve"),
    # J3: latest_sdss_sql shares the get_cached_results / get_adql_results
    # access interface (run_sdss_sql stores results in the adql_result_sets pool,
    # reusing the getter). The explicit latest_sdss_sql variable name is the
    # unique identifier.
    "latest_sdss_sql": ("get_cached_results", "get_adql_results", "latest_sdss_sql"),
    "latest_high_velocity_stars": ("get_cached_results", "get_adql_results", "latest_high_velocity_stars"),
}
_PLATFORM_REAL_DATA_READER_TOKENS = {
    # These helpers themselves trigger real archive / MAST / platform cache reads.
    # Even if the model declares data_source='latest_search' instead of
    # 'latest_lightcurve', G1.2 should not treat this as “no real data read”.
    "search_lightcurve",
    "download_and_clean_lightcurve",
    "transit_search",
}


def _summarize_ast_observations(code: str, limit: int = 24) -> str:
    """Return the identifiers actually seen by the AST check, helping the model self-correct from error messages."""
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError:
        return "AST observations unavailable because the code has a SyntaxError."

    calls: set[str] = set()
    names: set[str] = set()
    imports: set[str] = set()

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Name):
            names.add(node.id)
        elif isinstance(node, _ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, _ast.Call):
            func = node.func
            if isinstance(func, _ast.Name):
                calls.add(func.id)
            elif isinstance(func, _ast.Attribute):
                calls.add(func.attr)
        elif isinstance(node, _ast.ImportFrom):
            for alias in node.names:
                imports.add(alias.asname or alias.name)
        elif isinstance(node, _ast.Import):
            for alias in node.names:
                imports.add(alias.asname or alias.name.split(".")[0])

    def _fmt(label: str, values: set[str]) -> str | None:
        if not values:
            return None
        ordered = sorted(values)
        suffix = "" if len(ordered) <= limit else f", +{len(ordered) - limit} more"
        return f"{label}={ordered[:limit]}{suffix}"

    parts = [
        part for part in (
            _fmt("calls", calls),
            _fmt("names", names),
            _fmt("imports", imports),
        )
        if part
    ]
    if not parts:
        return "AST observed no function calls, variable names, or imports."
    return "AST observed " + "; ".join(parts) + "."


# X5 (PART X): per-session consecutive run_python call counter. Used to observe
# the "Nth run_python crashes in sandbox" pattern — B4/B5/B6 all crashed at
# call 3 or later but we lack data to pinpoint the root cause. Accumulate
# 1-2 rounds of B regressions before deciding whether to dig deeper into the sandbox.
_session_run_python_count: dict[str, int] = {}
_MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC = 10  # cap cardinality


def _bump_run_python_attempt_idx(session_id: str) -> int:
    """Return the 1-indexed call number of run_python in this session."""
    _session_run_python_count[session_id] = (
        _session_run_python_count.get(session_id, 0) + 1
    )
    return _session_run_python_count[session_id]


_ABS_MAG_CONTEXT_RE = re.compile(
    r"(?:M_G|M\s*_\s*G|abs(?:olute)?\s+(?:G\s+)?mag(?:nitude)?|absolute_magnitude)"
    r"[^-+\d\n]{0,40}"
    r"([-+]?(?:\d+(?:\.\d+)?|\.\d+))"
    r"(?:\s*(?:to|[-–—])\s*([-+]?(?:\d+(?:\.\d+)?|\.\d+)))?",
    re.I,
)


def _unphysical_cmd_magnitudes(payload: dict[str, Any]) -> list[float]:
    text_parts: list[str] = []
    for key in ("stdout", "stderr"):
        if payload.get(key):
            text_parts.append(str(payload[key]))
    variables = payload.get("variables")
    if variables:
        text_parts.append(str(variables))

    values: list[float] = []
    for match in _ABS_MAG_CONTEXT_RE.finditer("\n".join(text_parts)):
        for group in match.groups():
            if group is None:
                continue
            try:
                value = float(group)
            except ValueError:
                continue
            if math.isfinite(value) and (value < -30.0 or value > 20.0):
                values.append(value)
    return values


def _apply_cmd_sanity_guard(response: dict[str, Any]) -> dict[str, Any]:
    """Mark unphysical CMD absolute magnitudes as unciteable partial output."""
    bad_values = _unphysical_cmd_magnitudes(response)
    if not bad_values:
        return response

    warning = (
        "Unphysical CMD absolute magnitude detected "
        f"({', '.join(f'{v:g}' for v in bad_values[:4])}); likely parallax-unit "
        "or distance-modulus error. Do not cite CMD-derived magnitudes, age, "
        "distance, or extinction from this Python output."
    )
    warnings = list(response.get("warnings") or [])
    warnings.append(warning)
    response["warnings"] = warnings
    response["__tool_status__"] = "PARTIAL"
    response["analysis_status"] = "partial"
    response["__do_not_claim__"] = True
    response["__message_to_model__"] = warning
    response["cmd_sanity_error"] = "unphysical_absolute_magnitude"
    return response


async def _exec_run_python(inp: dict, python_session_id: str = "default") -> dict:
    """Execute Python code in sandboxed environment."""
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _search_result_cache
    from app.services.code_executor import execute_python

    code = inp.get("code", "")
    # G1.1: data_source contract.  AI must declare where the data comes
    # from.  Treatment of missing / unknown declarations:
    # - Missing entirely: look at the code and auto-classify via AST.
    #   B-S4 fix: the old default was "none_not_analyzing_real_data" which
    #   flagged legitimate code reading Gaia ADQL results as SYNTHETIC
    #   ("Numbers from synthetic tools are NOT from observations" warning
    #   next to real Gaia DR3 δ Cephei data).  If the code clearly reads
    #   a real cache (get_adql_results etc.), infer the source.  Otherwise
    #   fall back to synthetic.  AST walk keeps this precise.
    # - Known real source (latest_adql/search/lightcurve): validate that
    #   the code actually reads from it; mismatch → reject.
    # - "none_not_analyzing_real_data": explicit synthetic, marked SYNTHETIC.
    # - cached:<key> / fits:<path>: free-form, no static check.
    data_source = str(inp.get("data_source", "")).strip()
    if not data_source:
        try:
            from app.observability.metrics import record_counter
            record_counter("run_python_missing_data_source_total", 1.0)
        except Exception:
            pass
        # B-S4: AST-based auto-classification instead of blanket SYNTHETIC.
        # Looks for calls/names that mean the code is reading a real cache.
        auto_source: str | None = None
        try:
            import ast as _ast_auto
            tree = _ast_auto.parse(code)
            names_seen: set[str] = set()
            for node in _ast_auto.walk(tree):
                if isinstance(node, _ast_auto.Name):
                    names_seen.add(node.id)
                if isinstance(node, _ast_auto.Attribute):
                    names_seen.add(node.attr)
            # J3: presence of latest_sdss_sql / run_sdss_sql in the code indicates
            # an SDSS cache source. This check must come before get_adql_results
            # because run_sdss_sql reuses the ADQL cache pool; the variable name
            # is the only way to distinguish them.
            if any(t in names_seen for t in ("latest_sdss_sql", "run_sdss_sql")):
                auto_source = "latest_sdss_sql"
            elif any(t in names_seen for t in ("get_adql_results", "get_adql_result_sets")):
                auto_source = "latest_adql"
            elif "get_search_results" in names_seen:
                auto_source = "latest_search"
            elif any(t in names_seen for t in ("search_lightcurve", "download_and_clean_lightcurve")):
                auto_source = "latest_lightcurve"
            elif "get_cached_results" in names_seen:
                auto_source = "latest_adql"  # the most common cached source
            elif any(t in names_seen for t in ("load_fits", "fits")):
                auto_source = "fits:<autodetected>"
            # PART AD: user-supplied local data files (CSV / parquet) are real
            # data too. Without this the AI cannot declare a source for its own
            # uploaded data, so the run defaults to synthetic and the X3 reverse
            # check then rejects it for actually reading real data.
            elif any(t in names_seen for t in ("read_csv", "read_parquet", "load_csv")):
                auto_source = "user_file:<autodetected>"
        except Exception:
            pass

        if auto_source is not None:
            data_source = auto_source
            # Fire a counter so we can see how often AI forgets to declare
            # but we auto-recovered.
            try:
                from app.observability.metrics import record_counter
                record_counter("run_python_data_source_auto_inferred_total", 1.0, inferred=auto_source)
            except Exception:
                pass
        else:
            # Still couldn't tell — default to synthetic (safer).
            data_source = "none_not_analyzing_real_data"

    is_synthetic_declared = data_source == "none_not_analyzing_real_data"
    ast_suspicious_taint = False

    # PART AD: cached:<key> declares an inline cache handle. The old code
    # trusted it blindly ("free-form, no static check"), so a non-existent
    # key let the run proceed and the code could silently fall back to
    # fabrication. Validate the key resolves to live cached data first.
    if data_source.startswith("cached:"):
        cache_key = data_source[len("cached:"):].strip()
        if not cache_key or get_cached_results(cache_key) is None:
            now = time.time()
            available = sorted(
                k for k, (_, ts) in _search_result_cache.items()
                if now - ts <= _CACHE_TTL_SECONDS
            )
            return {
                "success": False,
                "error": (
                    f"data_source='cached:{cache_key}' but no live cached "
                    f"results exist under key '{cache_key}'. Available cache "
                    f"keys: {available or 'none'}. Fetch the data first "
                    f"(run_adql / search_objects / search_lightcurve / "
                    f"extract_literature_tables), then reference the correct "
                    f"cached:<key>."
                ),
                "error_class": "cached_key_not_found",
            }

    # X3 (PART X): reverse-direction data_source check.  If AI declared
    # synthetic but the code actually reads real archive/cache helpers,
    # the declaration is wrong — the output would be mis-labelled as
    # SYNTHETIC even though it's analyzing real data, confusing the user
    # and tainting the provenance trail.  Symmetric counterpart to G1.2
    # ("declared real but code is synthetic").
    #
    # B6 P-3 regression: AI ran DBSCAN on 252 real Gaia rows (fetched
    # via get_adql_results() earlier in the turn) but declared
    # data_source='none_not_analyzing_real_data', so the real output
    # got SYNTHETIC-stamped incorrectly.
    if is_synthetic_declared:
        _REAL_CACHE_READERS = (
            "get_cached_results(", "get_search_results(",
            "get_adql_results(", "get_adql_result_sets(",
            "get_latest_adql_result(", "load_fits(",
            # PART Y Batch 4: extend X3 reverse detection. Audit found AI
            # could declare 'none_not_analyzing_real_data' while the code
            # actually fetched real lightcurves / read real FITS / pulled
            # CSV — these readers were not in the X3 string list.
            "search_lightcurve(", "lightkurve.",
            "Table.read(", "fits.open(",
            "pd.read_csv(", "pd.read_parquet(",
            "load_votable(", "load_csv(",
            "astro.search_lightcurve(", "astro.download_and_clean_lightcurve(",
            "astroquery",
        )
        reads_real_cache = any(p in code for p in _REAL_CACHE_READERS)
        if reads_real_cache:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "incorrect_synthetic_declaration_total",
                    1.0,
                )
            except Exception:
                pass
            return {
                "success": False,
                "error": (
                    "Incorrect synthetic declaration: code accesses real "
                    "cache helpers (get_cached_results / get_search_results "
                    "/ get_adql_results / get_adql_result_sets / "
                    "get_latest_adql_result / load_fits) but data_source "
                    "was declared 'none_not_analyzing_real_data'. Change "
                    "data_source to 'latest_adql' / 'latest_search' / "
                    "'latest_lightcurve' / 'cached:<key>' / 'fits:<path>' / "
                    "'user_file:<path>' (your own CSV / parquet via pd.read_csv "
                    "/ pd.read_parquet / load_csv) "
                    "to match what the code actually reads. The SYNTHETIC "
                    "tag is reserved for code that genuinely does NOT "
                    "read any real data."
                ),
                "error_class": "incorrect_synthetic_declaration",
            }

    # G1.2: if declared a real source, the code should reference the
    # matching helper.  Skip validation for cached:... and fits:... which
    # carry the target inline.
    if data_source in _REAL_DATA_SOURCE_PATTERNS:
        expected_tokens = _REAL_DATA_SOURCE_PATTERNS[data_source]
        # H3.1: AST-based check instead of naive substring.
        # - String `in` picked up tokens in comments / docstrings, leaving
        #   false positives that Paper 1 reviewer hit (AI legitimate code
        #   failing because a helper was aliased).
        # - AST walk catches:
        #     * Direct calls: get_adql_results()
        #     * Aliased: helper = get_adql_results; helper()
        #     * Imports: from x import get_adql_results as fetch
        #     * Attribute calls: astro.get_adql_results()
        # Plus we always accept `get_cached_results(...)` as a valid
        # signal for any real data_source (the generic cache reader
        # from G6.2 can fetch any handle).
        import ast as _ast
        tokens_to_match = (
            set(expected_tokens)
            | {"get_cached_results"}
            | _PLATFORM_REAL_DATA_READER_TOKENS
        )
        found_in_ast = False
        try:
            tree = _ast.parse(code)
            for node in _ast.walk(tree):
                # Any Name node matching
                if isinstance(node, _ast.Name) and node.id in tokens_to_match:
                    found_in_ast = True
                    break
                # Attribute access (foo.bar or module.helper)
                if isinstance(node, _ast.Attribute) and node.attr in tokens_to_match:
                    found_in_ast = True
                    break
                # Import "from x import <token>" or "import <token>"
                if isinstance(node, _ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in tokens_to_match or (alias.asname and alias.asname in tokens_to_match):
                            found_in_ast = True
                            break
                if isinstance(node, _ast.Import):
                    for alias in node.names:
                        if alias.name in tokens_to_match or (alias.asname and alias.asname in tokens_to_match):
                            found_in_ast = True
                            break
                if found_in_ast:
                    break
        except SyntaxError:
            # Code has SyntaxError; sandbox will surface it next step.
            # Be permissive here and fall back to string match so we
            # don't reject on syntax issues alone.
            found_in_ast = any(tok in code for tok in tokens_to_match)

        # S1 (PART S): session-scoped history check. R9-NEW-1 regression:
        # the AI called `rows = get_adql_results()` in a previous cell and then
        # `df.groupby(...)` directly in the current cell, which was rejected.
        # If the expected token was called anywhere in the session history, it
        # also counts as passing — the subprocess replay prefix re-executes that
        # cell, so the current cell genuinely has access to real archive data.
        if not found_in_ast and python_session_id and python_session_id != "default":
            try:
                from app.services.code_executor import get_session_helper_calls
                history_tokens = get_session_helper_calls(python_session_id)
                if tokens_to_match & history_tokens:
                    found_in_ast = True
            except Exception:
                pass

        if not found_in_ast:
            observed_ast = _summarize_ast_observations(code)
            return {
                "success": False,
                "error": (
                    f"data_source='{data_source}' declared but the code does not "
                    f"call any of {sorted(tokens_to_match)} (checked via AST walk "
                    f"including aliases + imports). {observed_ast} Either (a) update "
                    f"your code to actually read that cache, (b) declare "
                    f"data_source='user_file:<path>' if you are reading your own "
                    f"uploaded CSV / parquet via pd.read_csv / pd.read_parquet / "
                    f"load_csv, or (c) declare "
                    f"data_source='none_not_analyzing_real_data' if you are "
                    f"intentionally generating synthetic data (which will be "
                    f"marked SYNTHETIC and forbidden from citation)."
                ),
                "error_class": "data_source_mismatch",
            }

    # G2: AST static analysis to catch contract violations where the AI
    # declares `latest_adql` but the code actually does np.random.normal().
    # Run detector early; if verdict=synthetic AND declared a real source,
    # reject; if verdict=suspicious, downgrade output to SYNTHETIC.
    try:
        from app.services.synthetic_code_detector import analyze as _analyze
        detection = _analyze(code)
        if detection.verdict == "synthetic" and not is_synthetic_declared:
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_detected_total", 1.0,
                    trigger="ast_contract_mismatch",
                    declared=data_source,
                )
            except Exception:
                pass
            return {
                "success": False,
                "error": (
                    f"G2 static-analysis detected this code fabricates data "
                    f"(np.random / suspicious keywords / no real-data readers) "
                    f"but data_source='{data_source}' was declared. "
                    f"Signals: has_np_random={detection.has_np_random}, "
                    f"has_time_linspace={detection.has_time_linspace}, "
                    f"has_schematic_phase_curve={getattr(detection, 'has_schematic_phase_curve', False)}, "
                    f"has_constant_redshift_sequence={detection.has_constant_redshift_sequence}, "
                    f"suspicious_keywords={detection.suspicious_keywords}, "
                    f"reads_real_data={detection.reads_real_data}. "
                    f"Either fix the code to read real data, declare "
                    f"data_source='none_not_analyzing_real_data', or emit "
                    f"<tools_returned_nothing/> if you intended to abstain."
                ),
                "error_class": "synthetic_declared_as_real",
                "detection": {
                    "verdict": detection.verdict,
                    "suspicious_keywords": detection.suspicious_keywords,
                    "has_np_random": detection.has_np_random,
                    "has_time_linspace": detection.has_time_linspace,
                    "has_schematic_phase_curve": getattr(detection, "has_schematic_phase_curve", False),
                    "has_constant_redshift_sequence": detection.has_constant_redshift_sequence,
                    "reads_real_data": detection.reads_real_data,
                },
            }
        elif detection.verdict == "suspicious" and not is_synthetic_declared:
            # Downgrade to synthetic at response time (below).  Record why.
            is_synthetic_declared = True
            ast_suspicious_taint = True
            try:
                from app.observability.metrics import record_counter
                record_counter(
                    "synthetic_detected_total", 1.0,
                    trigger="ast_suspicious",
                    declared=data_source,
                )
            except Exception:
                pass
        elif detection.verdict == "inert":
            # R5 O3: pure literal print / constant-arithmetic code (smoke test /
            # environment sanity check). Even when the AI declared
            # data_source='none...', do not mark SYNTHETIC — no synthetic data
            # is produced and no numbers enter the analysis chain.
            is_synthetic_declared = False
            try:
                from app.observability.metrics import record_counter
                record_counter("inert_code_exempted_from_synthetic_total", 1.0)
            except Exception:
                pass
    except Exception as det_exc:
        logger.debug("synthetic_code_detector failed: %s", det_exc)

    if not code.strip():
        # E2.3: previously returned a bare "No code provided" which the AI
        # would retry as another empty call.  Track via Prometheus so we
        # can see how often the LLM leaks this and surface an actionable
        # hint the AI can react to.
        try:
            from app.observability.metrics import record_counter
            record_counter("empty_tool_call_total", 1.0, tool="run_python")
        except Exception:
            pass
        return {
            "error": "run_python called with empty code — skipping.",
            "error_class": "empty_input",
            "success": False,
            "hint": (
                "Include the code to run in the `code` field.  If you meant to "
                "refer to a prior variable, include a short snippet like "
                "`print(members.head())` so the sandbox can execute it."
            ),
        }

    # G5: mode-dependent timeout.  fast=30s / normal=75s (default) / slow=300s.
    mode = str(inp.get("mode") or "normal").lower()
    mode_timeout = {"fast": 30.0, "normal": 75.0, "slow": 300.0}.get(mode, 75.0)

    # Run in executor with timeout to not block the event loop
    loop = asyncio.get_running_loop()
    auto_escalated = False
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(None, execute_python, code, None, python_session_id, mode_timeout),
            timeout=mode_timeout,
        )
    except asyncio.TimeoutError:
        # U2b (PART U): Round 10 observation — the AI frequently exceeds 75s on
        # the first lightkurve download with mode='normal', then manually switches
        # to mode='slow' in a subsequent turn. Eliminate the model round-trip:
        # if mode is not 'slow', automatically retry once with mode='slow' (300s).
        # Only one escalation is allowed to prevent infinite loops.
        if mode != "slow":
            try:
                result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None, execute_python, code, None, python_session_id, 300.0
                    ),
                    timeout=300.0,
                )
                auto_escalated = True
                mode = "slow"
                mode_timeout = 300.0
            except asyncio.TimeoutError:
                return {
                    "success": False,
                    "error": (
                        f"Code execution timed out after 300s even after auto-"
                        f"escalating to mode='slow' (initial mode={inp.get('mode') or 'normal'}). "
                        f"The task likely needs to be split into smaller chunks, "
                        f"or run as a background pipeline job."
                    ),
                    "error_class": "timeout",
                    "stdout": "",
                    "auto_escalated_mode": True,
                }
        else:
            return {
                "success": False,
                "error": (
                    f"Code execution timed out after {int(mode_timeout)}s (mode={mode}). "
                    f"For long computations (BLS / MCMC / bootstrap / PARSEC grid), "
                    f"split into smaller chunks or use a background pipeline job."
                ),
                "error_class": "timeout",
                "stdout": "",
            }

    auto_fix_note = None
    if not result.success and result.error:
        retry = _retryable_python_fix(code, result.error)
        if retry is not None:
            fixed_code, auto_fix_note = retry
            try:
                retry_result = await asyncio.wait_for(
                    loop.run_in_executor(None, execute_python, fixed_code, None, python_session_id, mode_timeout),
                    timeout=mode_timeout,
                )
                if retry_result.success:
                    result = retry_result
                else:
                    auto_fix_note = None
            except asyncio.TimeoutError:
                auto_fix_note = None

    response: dict = {
        "success": result.success,
        "stdout": result.stdout[:50_000] if result.stdout else "",
        "backend": getattr(result, "backend", "unknown"),
        "duration_ms": getattr(result, "duration_ms", 0),
        "exit_code": getattr(result, "exit_code", None),
        "mode": mode,  # reflects the mode actually used (may have been auto-escalated to 'slow' by U2b)
    }
    if auto_escalated:
        response["auto_escalated_mode"] = True
        response["note"] = (
            "run_python auto-escalated from mode='{}' to mode='slow' because "
            "initial run exceeded its timeout budget. Future similar calls "
            "should declare mode='slow' up front.".format(inp.get("mode") or "normal")
        )

    # R5 O2: non-zero exit_code overrides success=True. On a subprocess crash
    # the child sets payload['success']=True at startup; if the code crashes
    # before reaching the except block, the parent's SandboxResult.success
    # remains True even though proc.exitcode=1. Aligning them is the foundation
    # of the correct success semantics — otherwise the UI / AI sees
    # "success=true + exit_code=1" and cannot tell whether the call succeeded.
    _exit = response.get("exit_code")
    if _exit not in (None, 0) and response.get("success"):
        response["success"] = False
        response.setdefault("error", f"Subprocess exited with non-zero code {_exit}")
        response.setdefault("error_class", "sandbox_nonzero_exit")

    # F0.2: error-field tripwire.  Previously we only populated `error` when
    # `result.error` was truthy — so a sandbox that returned success=False
    # with an empty/None error (e.g. subprocess was SIGKILLed before it
    # could serialize its error message) produced a response dict with no
    # error key, surfacing on the frontend as the generic "Python sandbox
    # returned no message (check backend logs)" fallback.  Guarantee that
    # any failure carries a concrete, user-actionable message plus an
    # error_class the UI can chip-render.
    if result.error:
        response["error"] = result.error
        response["error_class"] = _classify_sandbox_error(result.error)
    elif not result.success:
        response["error"] = (
            f"Sandbox reported failure without a specific error message. "
            f"stdout_len={len(result.stdout or '')}, "
            f"stderr_len={len(result.stderr or '')}, "
            f"figures={len(result.figures or [])}.  This usually means the "
            f"sandbox subprocess was killed (OOM / SIGKILL / wall-clock "
            f"timeout) before it could send its result back."
        )
        response["error_class"] = "sandbox_crash"
        try:
            from app.observability.metrics import record_counter
            record_counter("sandbox_silent_failure_total", 1.0, tool="run_python")
        except Exception:
            pass
    if response.get("error_class") == "name_error":
        try:
            from app.services.code_executor import get_session_defined_names
            defined_names = get_session_defined_names(python_session_id)[:80]
            if defined_names:
                response["defined_names"] = defined_names
                response["hint"] = (
                    "NameError occurred. Reuse one of the defined session "
                    f"names instead of guessing: {', '.join(defined_names[:30])}"
                )
        except Exception:
            pass
    if response.get("error_class") == "key_error":
        missing_key = _missing_key_from_error(str(response.get("error") or ""))
        response["hint"] = (
            f"KeyError for column {missing_key!r}. Inspect available columns before indexing "
            "(for example: `rows = get_adql_results(); print(rows[0].keys())`), "
            f"then use `row.get({missing_key!r})` or the exact available column name. "
            "Do not invent a replacement source_id or assume every catalog row has one."
        )
    # R5 O1 + R6 post: stderr is ALWAYS written to response as a top-level field,
    # even when it is an empty string. From a diagnostic standpoint,
    # "stderr=''" and "stderr not set" are completely different signals:
    #   former = the child main ran and captured stderr; it genuinely produced nothing
    #   latter = the child crashed during Python interpreter startup / import;
    #            child main never ran; stderr went to uvicorn fd=2 (Render container
    #            logs), unreachable by the client
    # Users (and the AI) can infer the second case from the empty string alone
    # and know to check /api/admin/sandbox/health for Python-level stderr.
    # The legacy `traceback` field is kept for backwards-compat in failure cases.
    response["stderr"] = (result.stderr or "")[:10_000]
    if not result.stderr and _exit not in (None, 0):
        response["stderr_note"] = (
            "stderr field is empty DESPITE non-zero exit code. This means "
            "the subprocess crashed during Python interpreter startup / "
            "module import, before the child process could set up stderr "
            "capture. The real Python error went to the container's fd=2 "
            "(uvicorn stderr / Render log). Call GET /api/admin/sandbox/"
            "health from an admin client to reproduce with subprocess.Popen "
            "and see the actual Python traceback."
        )
    if result.stderr:
        response["traceback"] = result.stderr[:10_000]
    if result.figures:
        response["figures"] = result.figures[:10]  # max 10 figures
        response["figure_count"] = len(result.figures)
    if result.variables:
        response["variables"] = dict(list(result.variables.items())[:50])
    if result.variable_types:
        response["variable_types"] = dict(list(result.variable_types.items())[:50])
    if auto_fix_note:
        response["auto_fix_note"] = auto_fix_note

    response = _apply_cmd_sanity_guard(response)

    if response.get("success") is False and not (
        response.get("stdout")
        or response.get("figures")
        or response.get("variables")
    ):
        # A failure with no usable output must be marked FAILED directly,
        # so the frontend does not fall back to showing 'auto'.
        # Failures that still have stdout/figures/variables are downgraded to
        # PARTIAL by result_provenance.
        response.setdefault("__tool_status__", "FAILED")
        response.setdefault("analysis_status", "failed")

    # G1.3: SYNTHETIC banner.  If the AI declared that this run_python call
    # is NOT analyzing real data, prepend a banner and mark data_origin so
    # the zero-fabrication gate refuses to cite numbers from this run.
    if is_synthetic_declared:
        if ast_suspicious_taint:
            provenance_message = (
                f"This run_python call declared data_source={data_source!r}, "
                "but static analysis found unverified random/synthetic "
                "operations outside a recognised MCMC/bootstrap workflow. "
                "Its numerical output is NOT a claimable observational result."
            )
        else:
            provenance_message = (
                "This run_python call was declared data_source="
                "'none_not_analyzing_real_data'. Its numerical output is NOT "
                "from real observations."
            )
        banner = {
            "__tool_status__": "SYNTHETIC",
            "__do_not_claim__": True,
            "__message_to_model__": (
                provenance_message
                + " You MUST NOT use any facts, numbers, "
                "historical context, literature priors, physical interpretations, "
                "or conclusions from this call's stdout/variables/figures in "
                "your final reply. "
                "If the user asked for real data analysis, emit "
                "<tools_returned_nothing/> instead — do not use this synthetic "
                "run to stand in for a failed data fetch."
            ),
            "__suggested_next_step__": (
                "If the user wants real data, retry with a real data source "
                "(latest_adql / fits:<path> / etc.) or emit the abstention tag."
            ),
            "data_origin": "synthetic",
            "analysis_status": "simulated_demo",
        }
        # Banner keys go FIRST so the model reads them before payload
        combined = dict(banner)
        combined.update(response)
        # But preserve the banner-level data_origin override (response may
        # have its own; this output remains non-citeable synthetic even when
        # the sandbox itself failed).
        combined["data_origin"] = "synthetic"
        combined["__synthetic_declared__"] = not ast_suspicious_taint
        combined["__synthetic_detected__"] = ast_suspicious_taint
        error_class = str(combined.get("error_class") or "").lower()
        fatal_failure = (
            combined.get("success") is False
            or combined.get("__tool_status__") == "FAILED"
            or error_class in {"oom", "sandbox_crash", "subprocesscrash", "timeout"}
        )
        if fatal_failure:
            # R22: execution failure must win over the SYNTHETIC badge.  The
            # payload is still non-citeable, but the UI/model must see that
            # the sandbox crashed/timed out instead of treating it as a
            # completed synthetic demo.
            combined["__tool_status__"] = "FAILED"
            combined["analysis_status"] = "failed"
            combined["__message_to_model__"] = (
                banner["__message_to_model__"]
                + " The Python execution also failed; do not treat stdout, "
                "variables, figures, or conclusions as a completed synthetic result."
            )
        else:
            combined["analysis_status"] = "simulated_demo"
        try:
            from app.observability.metrics import record_counter
            record_counter(
                "synthetic_declared_total", 1.0,
                trigger="contract",
            )
        except Exception:
            pass
        return combined

    # X5 (PART X): record which call number in this session the sandbox crash
    # occurred on. B4/B5/B6 all showed crashes at call 3 or later but we lack
    # data to locate the root cause. Not fixing the root cause — just adding
    # monitoring. Capturing the error_class dimension also helps categorise by
    # type (oom / sandbox_crash / timeout, etc.).
    try:
        if not response.get("success"):
            from app.observability.metrics import record_counter
            attempt_idx = _bump_run_python_attempt_idx(python_session_id)
            # Cap attempt_idx label at 10 to bound Prometheus cardinality —
            # beyond 10 all go into the "10+" bucket.
            attempt_label = (
                str(attempt_idx) if attempt_idx < _MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC
                else f"{_MAX_TRACKED_ATTEMPT_IDX_FOR_METRIC}+"
            )
            record_counter(
                "sandbox_crash_by_attempt_idx_total",
                1.0,
                attempt_idx=attempt_label,
                error_class=str(response.get("error_class", "unknown"))[:32],
            )
        else:
            # Bump on success too, to keep attempt_idx in sync with the real call count
            _bump_run_python_attempt_idx(python_session_id)
    except Exception:
        pass

    return response


def _classify_sandbox_error(err: str) -> str:
    """F0.2: map a sandbox error string to a short class so the UI can
    render a status chip (e.g. "oom", "timeout", "sandbox_crash").
    """
    if not err:
        return "unknown"
    low = err.lower()
    if "textlanguageerror" in low or "non-english" in low:
        return "non_english_output"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "memoryerror" in low or "address-space" in low or "oom" in low:
        return "oom"
    if (
        "sigsegv" in low or "segmentation fault" in low or "signal 11" in low
        or "subprocesscrash" in low
    ):
        return "sandbox_crash"
    if "sigkill" in low or "signal 9" in low or "killed" in low:
        return "oom"
    if "incomplete payload" in low or "terminated without result" in low \
            or "without an error message" in low:
        return "sandbox_crash"
    if "nameerror" in low:
        return "name_error"
    if "keyerror" in low:
        return "key_error"
    if "importerror" in low or "modulenotfounderror" in low:
        return "import_error"
    if "systemexit" in low:
        return "system_exit"
    if "syntaxerror" in low or "indentationerror" in low:
        return "syntax_error"
    return "runtime_error"


def _missing_key_from_error(err: str) -> str:
    match = re.search(r"KeyError:\s*['\"]([^'\"]+)['\"]", err or "")
    return match.group(1) if match else "unknown"


def _retryable_python_fix(code: str, error: str) -> tuple[str, str] | None:
    if "Unknown format code 'd' for object of type 'float'" in error:
        fixed = re.sub(r":(\d*)d(?=})", lambda m: f":{m.group(1)}.0f", code)
        if fixed != code:
            return fixed, "Adjusted float formatting from integer `:d` to `:.0f` and retried."

    # KeyError for column names — try swapping case (e.g. 'SOURCE_ID' vs 'source_id')
    key_match = re.search(r"KeyError:\s*['\"](\w+)['\"]", error)
    if key_match:
        bad_key = key_match.group(1)
        alt_key = bad_key.lower() if bad_key == bad_key.upper() else bad_key.upper()
        if alt_key != bad_key:
            fixed = code.replace(f"'{bad_key}'", f"'{alt_key}'").replace(f'"{bad_key}"', f'"{alt_key}"')
            if fixed != code:
                return fixed, f"Swapped column name case from '{bad_key}' to '{alt_key}' and retried."

    return None
