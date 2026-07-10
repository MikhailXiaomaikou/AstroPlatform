from __future__ import annotations


def test_robust_summary_reports_median_mad_and_bootstrap_ci() -> None:
    from app.services.astro_statistics import robust_summary

    result = robust_summary([1, 2, 3, 4, 100], n_bootstrap=200, seed=7)

    assert result["success"] is True
    assert result["analysis_type"] == "robust_summary"
    assert result["median"] == 3.0
    assert result["mad_sigma"] > 1.0
    assert len(result["median_bootstrap_ci_16_84"]) == 2
    assert result["publication_ready"] is False
    assert result["analysis_tier"] == "exploratory"
    assert "sample_size_at_least_20" in result["publication_gate"]["reasons"]


def test_one_value_summary_is_descriptive_not_publication_ready() -> None:
    from app.services.astro_statistics import robust_summary

    result = robust_summary([42.0])

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False
    assert "finite_nonzero_sample_spread" in result["publication_gate"]["reasons"]


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
    assert result["publication_ready"] is False


def test_bootstrap_linear_regression_is_seed_reproducible() -> None:
    from app.services.astro_statistics import bootstrap_linear_regression

    x = [0, 1, 2, 3, 4, 5]
    y = [1, 2.9, 5.1, 7.0, 9.1, 11.0]

    first = bootstrap_linear_regression(x, y, n_bootstrap=200, seed=42)
    second = bootstrap_linear_regression(x, y, n_bootstrap=200, seed=42)

    assert first["slope_ci_16_84"] == second["slope_ci_16_84"]
    assert first["intercept_ci_16_84"] == second["intercept_ci_16_84"]
    assert first["publication_ready"] is False


def test_large_noisy_regression_can_be_preliminary_but_not_publication_ready() -> None:
    from app.services.astro_statistics import linear_regression

    x = [float(i) for i in range(30)]
    y = [1.0 + 2.0 * value + (0.2 if i % 2 else -0.2) for i, value in enumerate(x)]

    result = linear_regression(x, y)

    assert result["preliminary_ready"] is True
    assert result["supports_measurement_claims"] is True
    assert result["publication_ready"] is False
    assert result["publication_gate"]["reasons"] == [
        "inline_array_source_provenance_unverified"
    ]


def test_censored_summary_counts_upper_limits_without_claiming_distribution_fit() -> None:
    from app.services.astro_statistics import censored_summary

    result = censored_summary([1.0, 2.0, 3.0, 4.0], is_upper_limit=[False, True, False, True])

    assert result["success"] is True
    assert result["n_detections"] == 2
    assert result["n_upper_limits"] == 2
    assert result["publication_ready"] is False
    assert "survival-analysis likelihood" in result["caveat"]


def test_censored_summary_never_promotes_descriptive_counts_to_publication() -> None:
    from app.services.astro_statistics import censored_summary

    values = [float(i) for i in range(1, 26)]
    flags = [False] * 20 + [True] * 5
    result = censored_summary(values, is_upper_limit=flags)

    assert result["preliminary_ready"] is True
    assert result["publication_ready"] is False
    assert "descriptive_only_no_survival_likelihood" in result[
        "publication_gate"
    ]["reasons"]


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
    assert result["publication_ready"] is False


def test_inline_statistics_prompt_routes_to_toolbox_with_uniform_errors() -> None:
    from app.api.chat import _inline_statistics_tool_call_from_prompt

    call = _inline_statistics_tool_call_from_prompt(
        "我有一组真实观测点：x=[0,1,2,3,4,5]，y=[1,3,5,7,9,11]，"
        "每个点的x和y误差都是0.1。请用平台内置统计工具做线性回归。"
    )

    assert call is not None
    assert call["name"] == "astro_statistics_toolbox"
    assert call["input"]["analysis_type"] == "linear_regression"
    assert call["input"]["x"] == [0, 1, 2, 3, 4, 5]
    assert call["input"]["y"] == [1, 3, 5, 7, 9, 11]
    assert call["input"]["x_err"] == [0.1] * 6
    assert call["input"]["y_err"] == [0.1] * 6


def test_inline_statistics_prompt_ignores_arrays_without_regression_request() -> None:
    from app.api.chat import _inline_statistics_tool_call_from_prompt

    assert _inline_statistics_tool_call_from_prompt("x=[1,2,3], y=[4,5,6]") is None


def test_statistics_tool_grounded_summary_reports_regression_values() -> None:
    from app.api.chat import _statistics_tool_grounded_summary

    summary = _statistics_tool_grounded_summary([
        {
            "tool": "astro_statistics_toolbox",
            "result": {
                "success": True,
                "analysis_type": "linear_regression",
                "method": "odr",
                "n": 6,
                "slope": 2.0,
                "slope_stderr": 0.0,
                "intercept": 1.0,
                "intercept_stderr": 0.0,
                "residual_rms": 0.0,
                "publication_ready": True,
            },
        }
    ])

    assert summary is not None
    assert "astro_statistics_toolbox" in summary
    assert "Slope: 2" in summary
    assert "Intercept: 1" in summary
