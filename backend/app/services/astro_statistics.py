"""Small deterministic statistics toolbox for astronomy workflows.

The toolbox deliberately exposes named, auditable routines instead of asking
the model to hand-write statistics in `run_python` every time.  Heavy Bayesian
or domain-specific fitters still live in their dedicated services; this module
covers common first-pass analyses with clear caveats.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _as_finite_array(values: Any) -> np.ndarray:
    arr = np.asarray(values or [], dtype=float)
    return arr[np.isfinite(arr)]


def _rng(seed: int | None) -> np.random.Generator:
    return np.random.default_rng(20260502 if seed is None else int(seed))


def robust_summary(values: list[float], *, n_bootstrap: int = 1000, seed: int | None = None) -> dict[str, Any]:
    data = _as_finite_array(values)
    if data.size == 0:
        return {"success": False, "error": "No finite values supplied.", "error_class": "empty_data"}
    med = float(np.median(data))
    mad = float(1.4826 * np.median(np.abs(data - med)))
    mean = float(np.mean(data))
    std = float(np.std(data, ddof=1)) if data.size > 1 else 0.0
    boot_ci = None
    if data.size >= 2 and n_bootstrap > 0:
        rng = _rng(seed)
        boots = np.empty(int(n_bootstrap), dtype=float)
        for i in range(int(n_bootstrap)):
            boots[i] = float(np.median(rng.choice(data, size=data.size, replace=True)))
        boot_ci = [round(float(np.percentile(boots, 16)), 6), round(float(np.percentile(boots, 84)), 6)]
    return {
        "success": True,
        "analysis_type": "robust_summary",
        "n": int(data.size),
        "mean": round(mean, 6),
        "std": round(std, 6),
        "median": round(med, 6),
        "mad_sigma": round(mad, 6),
        "p16": round(float(np.percentile(data, 16)), 6),
        "p84": round(float(np.percentile(data, 84)), 6),
        "median_bootstrap_ci_16_84": boot_ci,
        "publication_ready": True,
    }


def linear_regression(
    x: list[float],
    y: list[float],
    *,
    x_err: list[float] | None = None,
    y_err: list[float] | None = None,
    method: str = "auto",
) -> dict[str, Any]:
    x_arr = np.asarray(x or [], dtype=float)
    y_arr = np.asarray(y or [], dtype=float)
    if x_arr.shape != y_arr.shape:
        return {"success": False, "error": "x and y must have the same length.", "error_class": "shape_mismatch"}
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if x_arr.size < 3:
        return {"success": False, "error": "Need at least 3 finite x/y pairs.", "error_class": "too_few_points"}

    method = (method or "auto").lower()
    if method not in {"auto", "ols", "weighted", "odr", "theil_sen"}:
        method = "auto"

    y_err_arr = _masked_optional(y_err, mask)
    x_err_arr = _masked_optional(x_err, mask)
    selected = method
    if selected == "auto":
        if x_err_arr is not None and y_err_arr is not None:
            selected = "odr"
        elif y_err_arr is not None:
            selected = "weighted"
        else:
            selected = "ols"

    try:
        if selected == "theil_sen":
            from scipy import stats

            slope, intercept, lo_slope, hi_slope = stats.theilslopes(y_arr, x_arr)
            return _linear_result(
                selected, x_arr, y_arr, float(slope), float(intercept),
                slope_err=(float(hi_slope) - float(lo_slope)) / 2.0,
                intercept_err=None,
                extra={"slope_ci_95": [round(float(lo_slope), 6), round(float(hi_slope), 6)]},
            )
        if selected == "weighted" and y_err_arr is not None:
            weights = np.where(y_err_arr > 0, 1.0 / (y_err_arr ** 2), 0.0)
            coeff, cov = np.polyfit(x_arr, y_arr, deg=1, w=np.sqrt(weights), cov=True)
            slope, intercept = float(coeff[0]), float(coeff[1])
            return _linear_result(
                selected, x_arr, y_arr, slope, intercept,
                slope_err=float(math.sqrt(max(cov[0, 0], 0.0))),
                intercept_err=float(math.sqrt(max(cov[1, 1], 0.0))),
            )
        if selected == "odr" and x_err_arr is not None and y_err_arr is not None:
            from scipy import odr

            def f(beta: np.ndarray, xx: np.ndarray) -> np.ndarray:
                return beta[0] * xx + beta[1]

            model = odr.Model(f)
            data = odr.RealData(x_arr, y_arr, sx=x_err_arr, sy=y_err_arr)
            initial = np.polyfit(x_arr, y_arr, deg=1)
            out = odr.ODR(data, model, beta0=[float(initial[0]), float(initial[1])]).run()
            slope, intercept = float(out.beta[0]), float(out.beta[1])
            sd = out.sd_beta if out.sd_beta is not None else [None, None]
            return _linear_result(
                selected, x_arr, y_arr, slope, intercept,
                slope_err=float(sd[0]) if sd[0] is not None else None,
                intercept_err=float(sd[1]) if sd[1] is not None else None,
                extra={"odr_stop_reason": [str(v) for v in out.stopreason]},
            )
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}", "error_class": "fit_failed"}

    from scipy import stats

    fit = stats.linregress(x_arr, y_arr)
    return _linear_result(
        "ols", x_arr, y_arr, float(fit.slope), float(fit.intercept),
        slope_err=float(fit.stderr) if fit.stderr is not None else None,
        intercept_err=float(getattr(fit, "intercept_stderr", math.nan))
        if math.isfinite(float(getattr(fit, "intercept_stderr", math.nan)))
        else None,
        extra={"pearson_r": round(float(fit.rvalue), 6), "pearson_p": round(float(fit.pvalue), 6)},
    )


def bootstrap_linear_regression(
    x: list[float],
    y: list[float],
    *,
    n_bootstrap: int = 2000,
    seed: int | None = None,
) -> dict[str, Any]:
    x_arr = np.asarray(x or [], dtype=float)
    y_arr = np.asarray(y or [], dtype=float)
    if x_arr.shape != y_arr.shape:
        return {"success": False, "error": "x and y must have the same length.", "error_class": "shape_mismatch"}
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if x_arr.size < 3:
        return {"success": False, "error": "Need at least 3 finite x/y pairs.", "error_class": "too_few_points"}
    rng = _rng(seed)
    slopes = np.empty(int(n_bootstrap), dtype=float)
    intercepts = np.empty(int(n_bootstrap), dtype=float)
    for i in range(int(n_bootstrap)):
        idx = rng.integers(0, x_arr.size, size=x_arr.size)
        try:
            coeff = np.polyfit(x_arr[idx], y_arr[idx], deg=1)
            slopes[i] = coeff[0]
            intercepts[i] = coeff[1]
        except Exception:
            slopes[i] = np.nan
            intercepts[i] = np.nan
    slopes = slopes[np.isfinite(slopes)]
    intercepts = intercepts[np.isfinite(intercepts)]
    if slopes.size == 0:
        return {"success": False, "error": "All bootstrap fits failed.", "error_class": "fit_failed"}
    point = linear_regression(x_arr.tolist(), y_arr.tolist(), method="ols")
    point.update({
        "analysis_type": "bootstrap_linear_regression",
        "n_bootstrap": int(n_bootstrap),
        "slope_ci_16_84": [round(float(np.percentile(slopes, 16)), 6), round(float(np.percentile(slopes, 84)), 6)],
        "intercept_ci_16_84": [round(float(np.percentile(intercepts, 16)), 6), round(float(np.percentile(intercepts, 84)), 6)],
    })
    return point


def censored_summary(values: list[float], *, is_upper_limit: list[bool]) -> dict[str, Any]:
    vals = np.asarray(values or [], dtype=float)
    flags = np.asarray(is_upper_limit or [], dtype=bool)
    if vals.shape != flags.shape:
        return {"success": False, "error": "values and is_upper_limit must have the same length.", "error_class": "shape_mismatch"}
    finite = np.isfinite(vals)
    vals = vals[finite]
    flags = flags[finite]
    detected = vals[~flags]
    limits = vals[flags]
    out = {
        "success": True,
        "analysis_type": "censored_summary",
        "n": int(vals.size),
        "n_detections": int(detected.size),
        "n_upper_limits": int(limits.size),
        "upper_limit_fraction": round(float(limits.size / vals.size), 6) if vals.size else None,
        "publication_ready": detected.size >= 3,
        "caveat": (
            "This is a descriptive censored-data summary. Use a dedicated "
            "survival-analysis likelihood before claiming distribution parameters."
        ),
    }
    if detected.size:
        out["detected_median"] = round(float(np.median(detected)), 6)
        out["detected_range"] = [round(float(np.min(detected)), 6), round(float(np.max(detected)), 6)]
    if limits.size:
        out["upper_limit_range"] = [round(float(np.min(limits)), 6), round(float(np.max(limits)), 6)]
    return out


def run_statistics_toolbox(inp: dict[str, Any]) -> dict[str, Any]:
    analysis_type = str(inp.get("analysis_type") or "robust_summary").strip().lower()
    seed = inp.get("seed")
    if analysis_type == "robust_summary":
        return robust_summary(
            inp.get("values") or [],
            n_bootstrap=int(inp.get("n_bootstrap") or 1000),
            seed=int(seed) if seed is not None else None,
        )
    if analysis_type == "linear_regression":
        return linear_regression(
            inp.get("x") or [],
            inp.get("y") or [],
            x_err=inp.get("x_err"),
            y_err=inp.get("y_err"),
            method=str(inp.get("method") or "auto"),
        )
    if analysis_type == "bootstrap_linear_regression":
        return bootstrap_linear_regression(
            inp.get("x") or [],
            inp.get("y") or [],
            n_bootstrap=int(inp.get("n_bootstrap") or 2000),
            seed=int(seed) if seed is not None else None,
        )
    if analysis_type == "censored_summary":
        return censored_summary(inp.get("values") or [], is_upper_limit=inp.get("is_upper_limit") or [])
    return {
        "success": False,
        "error": f"Unknown analysis_type {analysis_type!r}.",
        "error_class": "unknown_analysis_type",
    }


def _masked_optional(values: list[float] | None, mask: np.ndarray) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.asarray(values, dtype=float)
    if arr.shape != mask.shape:
        return None
    arr = arr[mask]
    return arr if np.all(np.isfinite(arr)) else None


def _linear_result(
    method: str,
    x: np.ndarray,
    y: np.ndarray,
    slope: float,
    intercept: float,
    *,
    slope_err: float | None,
    intercept_err: float | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    y_model = slope * x + intercept
    residuals = y - y_model
    result = {
        "success": True,
        "analysis_type": "linear_regression",
        "method": method,
        "n": int(x.size),
        "slope": round(slope, 6),
        "intercept": round(intercept, 6),
        "slope_stderr": round(float(slope_err), 6) if slope_err is not None else None,
        "intercept_stderr": round(float(intercept_err), 6) if intercept_err is not None else None,
        "residual_rms": round(float(np.sqrt(np.mean(residuals ** 2))), 6),
        "publication_ready": bool(x.size >= 3),
        "supports_measurement_claims": bool(x.size >= 3),
    }
    if extra:
        result.update(extra)
    return result
