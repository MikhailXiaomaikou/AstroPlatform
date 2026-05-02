from __future__ import annotations


def test_robust_summary_reports_median_mad_and_bootstrap_ci() -> None:
    from app.services.astro_statistics import robust_summary

    result = robust_summary([1, 2, 3, 4, 100], n_bootstrap=200, seed=7)

    assert result["success"] is True
    assert result["analysis_type"] == "robust_summary"
    assert result["median"] == 3.0
    assert result["mad_sigma"] > 1.0
    assert len(result["median_bootstrap_ci_16_84"]) == 2


def test_linear_regression_uses_odr_when_two_axis_errors_available() -> None:
    from app.services.astro_statistics import linear_regression

    x = [0, 1, 2, 3, 4, 5]
    y = [1, 3, 5, 7, 9, 11]
    err = [0.1] * 6

    result = linear_regression(x, y, x_err=err, y_err=err, method="auto")

    assert result["success"] is True
    assert result["method"] == "odr"
    assert abs(result["slope"] - 2.0) < 1e-3
    assert abs(result["intercept"] - 1.0) < 1e-3


def test_bootstrap_linear_regression_is_seed_reproducible() -> None:
    from app.services.astro_statistics import bootstrap_linear_regression

    x = [0, 1, 2, 3, 4, 5]
    y = [1, 2.9, 5.1, 7.0, 9.1, 11.0]

    first = bootstrap_linear_regression(x, y, n_bootstrap=200, seed=42)
    second = bootstrap_linear_regression(x, y, n_bootstrap=200, seed=42)

    assert first["slope_ci_16_84"] == second["slope_ci_16_84"]
    assert first["intercept_ci_16_84"] == second["intercept_ci_16_84"]


def test_censored_summary_counts_upper_limits_without_claiming_distribution_fit() -> None:
    from app.services.astro_statistics import censored_summary

    result = censored_summary([1.0, 2.0, 3.0, 4.0], is_upper_limit=[False, True, False, True])

    assert result["success"] is True
    assert result["n_detections"] == 2
    assert result["n_upper_limits"] == 2
    assert result["publication_ready"] is False
    assert "survival-analysis likelihood" in result["caveat"]


def test_astro_statistics_toolbox_dispatches_regression() -> None:
    from app.services.ai_tools import _exec_astro_statistics_toolbox

    result = _exec_astro_statistics_toolbox({
        "analysis_type": "linear_regression",
        "x": [0, 1, 2],
        "y": [1, 3, 5],
    })

    assert result["success"] is True
    assert result["tool"] == "astro_statistics_toolbox"
    assert result["slope"] == 2.0
