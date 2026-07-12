"""TimeSeriesAnalysis node — Lomb-Scargle period detection and variability analysis."""

import numpy as np


def timeseries_analysis(input_data: dict, params: dict) -> dict:
    """Run time-series period search and variability analysis on light curve data.

    params:
        min_period: float — minimum period to search in days (default 0.1)
        max_period: float — maximum period to search in days (default 100)
        n_frequencies: int — number of frequency grid points (default 10000)
        fap_method: str — auto/bootstrap/baluev (default auto)
        n_bootstrap: int — bootstrap trials when applicable (default 200)
        random_seed: int — deterministic bootstrap seed
        time_col: str — key for time array in data (default "time")
        mag_col: str — key for magnitude array in data (default "mag")
        mag_err_col: str — key for magnitude error array in data (default "mag_err")
    """
    from app.services.astro_analysis import (
        lomb_scargle_period,
        variability_indices,
        classify_variable,
        phase_fold,
    )

    min_period = params.get("min_period", 0.1)
    max_period = params.get("max_period", 100)
    n_frequencies = params.get("n_frequencies", 10000)
    fap_method = params.get("fap_method", "auto")
    n_bootstrap = params.get("n_bootstrap", 200)
    raw_random_seed = params.get("random_seed")
    random_seed = int(raw_random_seed) if raw_random_seed is not None else None
    time_col = params.get("time_col", "time")
    mag_col = params.get("mag_col", "mag")
    mag_err_col = params.get("mag_err_col", "mag_err")

    data = input_data.get("data", {})

    time = np.array(data.get(time_col, []), dtype=float)
    mag = np.array(data.get(mag_col, []), dtype=float)

    if len(time) == 0 or len(mag) == 0:
        raise ValueError("TimeSeriesAnalysis: empty input arrays")

    mag_err = None
    if mag_err_col in data and len(data[mag_err_col]) > 0:
        mag_err = np.array(data[mag_err_col], dtype=float)

    # Period search
    period_result = lomb_scargle_period(
        time, mag, mag_err=mag_err,
        min_period=min_period, max_period=max_period,
        n_frequencies=n_frequencies,
        fap_method=fap_method, n_bootstrap=n_bootstrap,
        random_seed=random_seed,
    )

    # Variability indices
    var_indices = variability_indices(time, mag, mag_err=mag_err)

    # Classification
    classification = classify_variable(var_indices, period=period_result["best_period"])

    # Phase-fold at best period
    phases = phase_fold(time, period_result["best_period"]).tolist()

    # Strip large arrays from period_result to keep output manageable
    period_summary = {
        "best_period": period_result["best_period"],
        "best_power": period_result["best_power"],
        "fap": period_result["fap"],
        "fap_method": period_result["fap_method"],
        "random_seed": period_result.get("random_seed"),
        "top_periods": period_result["top_periods"],
    }

    return {
        **input_data,
        "period_result": period_summary,
        "variability_indices": var_indices,
        "classification": classification,
        "random_seed": period_result.get("random_seed"),
        "data": {
            **data,
            "phase": phases,
        },
    }
