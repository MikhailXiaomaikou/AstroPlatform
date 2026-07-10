"""Spectral-measurement preparation helpers + astro statistics toolbox.

Moved verbatim out of app/services/ai_tools/__init__.py (H2 split,
2026-07-03). Tool schemas here cover: prepare_spectral_measurements, astro_statistics_toolbox.
Schemas are reassembled into TOOLS (exact pre-split order) and tool
calls are still dispatched by _execute_tool_inner in the package
__init__ — this module is an implementation detail, import from
app.services.ai_tools.
"""

import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np

TOOL_SCHEMAS = [
    {
        "name": "prepare_spectral_measurements",
        "description": (
            "Validate and summarize cited spectral line measurement rows from a "
            "literature-table cache. Use this for any emission/absorption line "
            "sample ([CII], CO, Halpha, Lyalpha, [OIII], etc.) before fitting, "
            "exporting, or comparing surveys. It reports line inventory, fit-ready "
            "row counts, missing fields, citation counts, and ranges; it does not "
            "fit a relation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cache_key": {
                    "type": "string",
                    "description": "Cache key from extract_literature_tables. Default: latest_literature_tables.",
                },
                "line_id": {
                    "type": "string",
                    "description": "Optional line filter, e.g. [CII], CO(1-0), Halpha, Lyalpha, [OIII] 5007.",
                },
                "min_fit_rows": {
                    "type": "integer",
                    "description": "Minimum complete cited rows required to mark the sample fit-ready. Default: 5.",
                },
            },
        },
    },
    {
        "name": "astro_statistics_toolbox",
        "description": (
            "Run deterministic statistical helpers on supplied arrays: robust summary, "
            "OLS/weighted/ODR/Theil-Sen linear regression, bootstrap linear regression, "
            "and descriptive censored-data summaries for upper limits. Prefer this over "
            "ad-hoc run_python for common statistics when the data arrays are already "
            "available from real tools. Outputs are preliminary supplied-array "
            "calculations, never a publication certificate by themselves."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "analysis_type": {
                    "type": "string",
                    "enum": ["robust_summary", "linear_regression", "bootstrap_linear_regression", "censored_summary"],
                    "description": "Statistic to run.",
                },
                "values": {"type": "array", "items": {"type": "number"}, "description": "Values for robust_summary or censored_summary."},
                "is_upper_limit": {"type": "array", "items": {"type": "boolean"}, "description": "Flags for censored_summary; true means upper limit."},
                "x": {"type": "array", "items": {"type": "number"}, "description": "x values for regression."},
                "y": {"type": "array", "items": {"type": "number"}, "description": "y values for regression."},
                "x_err": {"type": "array", "items": {"type": "number"}, "description": "Optional x uncertainties."},
                "y_err": {"type": "array", "items": {"type": "number"}, "description": "Optional y uncertainties."},
                "method": {"type": "string", "enum": ["auto", "ols", "weighted", "odr", "theil_sen"], "description": "Regression method."},
                "n_bootstrap": {"type": "integer", "description": "Bootstrap iterations when applicable."},
                "seed": {"type": "integer", "description": "Random seed for bootstrap resampling."},
            },
            "required": ["analysis_type"],
        },
    },
]


def _line_matches_filter(row: dict[str, Any], line_filter: str) -> bool:
    target = re.sub(r"[^a-z0-9]+", "", (line_filter or "").lower())
    if not target:
        return True
    line_text = " ".join(
        str(row.get(key) or "")
        for key in ("line_id", "transition", "line", "table_label")
    )
    raw_values = row.get("raw_values")
    if isinstance(raw_values, dict):
        line_text += " " + " ".join(str(v) for v in raw_values.values())
    normalized = re.sub(r"[^a-z0-9]+", "", line_text.lower())
    if not normalized:
        # Normalized literature-table rows with log L + FWHM and no explicit
        # line label are still better handled by this typed fitter than by
        # model-authored synthetic Python.
        return True
    return target in normalized or ("cii" in target and "cii" in normalized)


def _finite_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _row_has_citation(row: dict[str, Any]) -> bool:
    citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
    return bool(
        row.get("bibcode") or row.get("arxiv_id")
        or citation.get("bibcode") or citation.get("arxiv_id")
        or citation.get("doi")
    )


def _build_censored_upper_limit_row(
    row: dict[str, Any],
    flags: list,
    idx: int,
    *,
    require_citation: bool,
) -> tuple[dict[str, Any] | None, str]:
    """Qualify a limit-flagged row as a Kelly 2007 censored (upper-limit) point.

    Physics discipline (2026-06-12): linmix censors y ONLY — x must be a
    genuine observation. A true line NON-detection has no measured FWHM, so
    a censorable row must carry a tabulated FWHM **and** its error verbatim
    from the paper (for non-detections that width is typically an assumed /
    companion-line value — the fit result declares this; we never invent x).
    Lower limits ('>') and FWHM-limited rows are not supported by the
    formalism and stay rejected. Returns (row_dict, "") or (None, reason).
    """
    flag_text = " ".join(str(f).lower() for f in flags)
    if "fwhm_limit" in flag_text:
        return None, "censored_fwhm_limited"
    raw_values = row.get("raw_values") if isinstance(row.get("raw_values"), dict) else {}
    raw_luminosity = str(raw_values.get("luminosity") or "")
    if ">" in raw_luminosity:
        return None, "censored_lower_limit_unsupported"
    if "<" not in raw_luminosity:
        # Cannot verify the limit DIRECTION from the verbatim cell — do not
        # guess which way the inequality points.
        return None, "censored_direction_unverifiable"
    if require_citation and not _row_has_citation(row):
        return None, "missing_citation"
    log_luminosity = _finite_float(row.get("log_luminosity"))
    if log_luminosity is None:
        lin_value = _finite_float(row.get("luminosity"))
        if lin_value is not None and 3.0 <= lin_value <= 13.0:
            log_luminosity = lin_value  # same legacy-cache fallback as detections
    fwhm = _finite_float(row.get("fwhm_km_s"))
    fwhm_err = _finite_float(row.get("fwhm_err_km_s"))
    if log_luminosity is None:
        return None, "censored_missing_limit_value"
    if fwhm is None or fwhm <= 0:
        return None, "censored_missing_fwhm"
    if fwhm_err is None or fwhm_err <= 0:
        return None, "censored_missing_fwhm_err"
    citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
    return {
        "source_name": row.get("source_name"),
        "redshift": row.get("redshift"),
        "line_id": row.get("line_id"),
        "log_luminosity": log_luminosity,          # the upper-limit value
        "log_luminosity_err": _finite_float(row.get("log_luminosity_err")),  # often None
        "fwhm_km_s": fwhm,
        "fwhm_err_km_s": fwhm_err,
        "is_upper_limit": True,
        # Carried so censored rows pass through the SAME post-acceptance
        # stages as detections (cosmology recompute, unit conversion,
        # lensing guard, cosmology-mismatch scan) — a censored row must
        # never dodge a transform or a guard the detections obey.
        "is_lensed": row.get("is_lensed"),
        "mu_lens": row.get("mu_lens"),
        "_demagnified": row.get("_demagnified"),
        "source_cosmology": row.get("source_cosmology"),
        "table_label": row.get("table_label") or citation.get("table_label"),
        "bibcode": row.get("bibcode") or citation.get("bibcode"),
        "arxiv_id": row.get("arxiv_id") or citation.get("arxiv_id"),
        "row_index": row.get("row_index", idx),
        "citation": citation,
    }, ""


def _load_user_csv_measurements(
    path: str,
    column_mapping: dict[str, Any] | None,
    line_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read a user-uploaded CSV into the line-measurement schema (3B, 2026-06-11).

    The path must point inside the general-upload area ("uploads/...");
    reading goes through app.storage.download_fits, whose _validate_path
    resolve()+relative_to guard blocks traversal. Column resolution reuses
    the extraction-side normalizer (incl. the user-confirmed column_mapping
    from 3A), so values come verbatim from the CSV cells. Rows carry
    citation={"type": "user_upload", ...} which deliberately does NOT
    satisfy _row_has_citation — user data is claimable as USER data
    (input_data_origin "user_uploaded", mirroring cosmology_mcmc's
    CLAIMABLE_INPUT_ORIGINS), never as a literature measurement.
    """
    import csv
    import io

    if not path.startswith("uploads/"):
        return [], (
            "user_file must reference an uploaded file path beginning with "
            "'uploads/' (upload via POST /api/data/files/upload first)."
        )
    if not path.lower().endswith(".csv"):
        return [], "user_file must be a .csv file."
    try:
        from app.storage import download_fits

        raw = download_fits(path)
    except Exception as exc:
        return [], f"could not read user_file {path!r}: {exc}"
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    table_rows = [r for r in csv.reader(io.StringIO(text)) if any(str(c).strip() for c in r)]
    if len(table_rows) < 2:
        return [], "user_file CSV needs a header row plus at least one data row."
    columns = [str(c).strip() for c in table_rows[0]]
    table = {
        "table_id": "user_csv",
        "name": path,
        "caption": "",
        "columns": columns,
        "rows": [[str(c) for c in r] for r in table_rows[1:]],
        "row_citations": [],
    }
    from app.api.arxiv import _normalize_line_measurements

    measurements = _normalize_line_measurements([table], column_mapping=column_mapping)
    for measurement in measurements:
        measurement["line_id"] = measurement.get("line_id") or line_id
        measurement["citation"] = {"type": "user_upload", "source_file": path}
    if not measurements:
        return [], (
            "no measurement rows could be normalized from the CSV — header "
            f"columns were {columns}. Retry with column_mapping="
            "{field: header-or-index} for source_name / redshift / "
            "log_luminosity / fwhm_km_s."
        )
    return measurements, None


# ── M4 helpers: subsample significance + demagnify_sample ──────────


def _bootstrap_ols_betas(
    x: "np.ndarray", y: "np.ndarray", n_boot: int, rng: "np.random.Generator",
) -> "np.ndarray":
    """Return n_boot bootstrap-resampled OLS slopes from (x, y).

    Pure numpy + scipy; no x-axis errors are propagated (bootstrap
    over rows is an OLS-friendly approximation).  Caller carries the
    'x_errors not propagated in bootstrap' caveat into reporting.
    """
    import numpy as np
    n = len(x)
    if n < 2:
        return np.zeros(0, dtype=float)
    betas = np.empty(n_boot, dtype=float)
    try:
        from scipy import stats as _stats
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            xs = x[idx]
            ys = y[idx]
            try:
                fit = _stats.linregress(xs, ys)
                betas[i] = float(fit.slope)
            except Exception:
                betas[i] = float("nan")
    except Exception:
        for i in range(n_boot):
            idx = rng.integers(0, n, size=n)
            try:
                betas[i] = float(np.polyfit(x[idx], y[idx], 1)[0])
            except Exception:
                betas[i] = float("nan")
    return betas[~np.isnan(betas)]


def _subsample_significance_from_betas(
    beta1: "np.ndarray", beta2: "np.ndarray",
) -> dict[str, Any]:
    """Compute Δβ summary from two bootstrap / posterior β arrays.

    Works for both bootstrap-OLS arrays and Bayesian-posterior arrays
    because the math is identical: pair them up via shuffled indices,
    take the per-pair difference, and compute the two-sided p-value
    that Δβ has crossed zero.
    """
    import numpy as np
    if beta1.size == 0 or beta2.size == 0:
        return {
            "delta_beta": None,
            "delta_beta_stderr": None,
            "p_value": None,
            "hdi_overlap": None,
            "interpretation": "insufficient_samples",
        }
    n = min(beta1.size, beta2.size)
    # Pair by random shuffle so we don't rely on bootstrap iteration
    # order having any meaning across the two subsamples.
    rng = np.random.default_rng(0)
    perm1 = rng.permutation(beta1.size)[:n]
    perm2 = rng.permutation(beta2.size)[:n]
    delta = beta1[perm1] - beta2[perm2]
    delta_mean = float(np.mean(delta))
    delta_std = float(np.std(delta))
    # Two-sided posterior-tail probability: probability mass on the side of
    # zero opposite the mean; ×2 for two-sided.  Capped at 1.0.  This is not
    # a frequentist p-value, so expose an explicitly named field and keep the
    # old p_value key as a deprecated compatibility alias for one release.
    if delta_mean >= 0:
        tail = float(np.mean(delta < 0))
    else:
        tail = float(np.mean(delta > 0))
    tail_probability_two_sided = float(min(1.0, 2.0 * tail))
    # Central 94% interval overlap (cheap proxy: do the central intervals
    # overlap?).  Useful as a categorical hint; this is not an HDI.
    lo1, hi1 = float(np.percentile(beta1, 3)), float(np.percentile(beta1, 97))
    lo2, hi2 = float(np.percentile(beta2, 3)), float(np.percentile(beta2, 97))
    overlap = max(0.0, min(hi1, hi2) - max(lo1, lo2))
    pooled = max(hi1 - lo1, hi2 - lo2, 1e-12)
    hdi_overlap_frac = overlap / pooled
    if tail_probability_two_sided < 0.01:
        interpretation = "significantly_different"
    elif tail_probability_two_sided < 0.05:
        interpretation = "marginal_significance"
    elif tail_probability_two_sided < 0.32:
        interpretation = "weak_evidence"
    else:
        interpretation = "consistent"
    return {
        "delta_beta": round(delta_mean, 6),
        "delta_beta_stderr": round(delta_std, 6),
        "tail_probability_two_sided": round(tail_probability_two_sided, 6),
        "central_interval_overlap_fraction": round(hdi_overlap_frac, 4),
        # Deprecated aliases.  Kept so older UI/tests do not break while
        # callers migrate to the scientifically precise field names above.
        "p_value": round(tail_probability_two_sided, 6),
        "hdi_overlap_fraction": round(hdi_overlap_frac, 4),
        "interpretation": interpretation,
    }


def _split_rows_by_redshift(
    accepted: list[dict[str, Any]],
    splits: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Apply a list of redshift-bin filters to accepted rows.

    Each split entry: ``{"name": "z<1", "z_min": ..., "z_max": ...}``
    where z_min/z_max are inclusive lower / exclusive upper bounds.
    Returns ``[(name, [rows...]), ...]`` preserving input order.
    """
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for split in splits or []:
        if not isinstance(split, dict):
            continue
        name = str(split.get("name") or "subsample").strip() or "subsample"
        z_min = split.get("z_min")
        z_max = split.get("z_max")
        sub: list[dict[str, Any]] = []
        for row in accepted:
            z = _finite_float(row.get("redshift"))
            if z is None:
                continue
            if z_min is not None and z < float(z_min):
                continue
            if z_max is not None and z >= float(z_max):
                continue
            sub.append(row)
        out.append((name, sub))
    return out


def _exec_prepare_spectral_measurements(inp: dict, python_session_id: str = "default") -> dict:
    # Lazy package import: tests monkeypatch these names on app.services.ai_tools;
    # resolving at call time preserves pre-split behavior (module globals == package namespace).
    from app.services.ai_tools import _resolve_literature_measurement_cache
    cache_key = str(inp.get("cache_key") or "latest_literature_tables").strip() or "latest_literature_tables"
    rows, resolved_cache_key = _resolve_literature_measurement_cache(cache_key, python_session_id)
    inline_rows = inp.get("rows")
    if not rows and isinstance(inline_rows, list):
        rows = [row for row in inline_rows if isinstance(row, dict)]
        resolved_cache_key = "inline_rows"
    if not rows:
        return {
            "success": False,
            "__tool_status__": "EMPTY",
            "analysis_status": "empty",
            "tool": "prepare_spectral_measurements",
            "error": f"No cached line_measurements found for cache_key={cache_key!r}.",
            "error_class": "missing_measurement_cache",
            "cache_key": cache_key,
            "__message_to_model__": (
                "No row-level spectral measurements are cached. Run "
                "extract_literature_tables first, then retry this workbench."
            ),
        }
    from app.services.spectral_measurement_workbench import prepare_spectral_measurements

    result = prepare_spectral_measurements(
        rows,
        line_id=inp.get("line_id"),
        min_fit_rows=int(inp.get("min_fit_rows") or 5),
    )
    result["cache_key"] = resolved_cache_key
    result["source_cache_key"] = resolved_cache_key
    if not result.get("fit_ready"):
        result["__tool_status__"] = "PARTIAL"
        result["analysis_status"] = "partial"
        result["__do_not_claim__"] = True
        result["__message_to_model__"] = (
            "The spectral measurement workbench found rows, but too few complete "
            "cited measurements are fit-ready. Report the gap; do not claim line "
            "statistics or fitted relations from this sample."
        )
    return result


def _exec_astro_statistics_toolbox(inp: dict) -> dict:
    from app.services.astro_statistics import run_statistics_toolbox

    result = run_statistics_toolbox(inp)
    result.setdefault("tool", "astro_statistics_toolbox")
    if result.get("success") is False:
        result.setdefault("__tool_status__", "FAILED")
        result.setdefault("__do_not_claim__", True)
    return result
