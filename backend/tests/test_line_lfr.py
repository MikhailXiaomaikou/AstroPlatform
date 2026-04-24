from __future__ import annotations


def _measurement_rows(n: int = 6) -> list[dict]:
    rows = []
    for idx in range(n):
        fwhm = 100 + idx * 50
        x = __import__("math").log10(fwhm / 100.0)
        rows.append({
            "source_name": f"ALPINE_{idx:03d}",
            "line_id": "[CII] 158um",
            "log_luminosity": 8.0 + 0.5 * x,
            "fwhm_km_s": float(fwhm),
            "quality_flags": [],
            "table_label": "Table 2",
            "citation": {
                "bibcode": "arXiv:2211.04968",
                "arxiv_id": "2211.04968",
                "authors": ["Example, A."],
                "year": "2022",
                "table_label": "Table 2",
            },
            "bibcode": "arXiv:2211.04968",
            "arxiv_id": "2211.04968",
        })
    return rows


def test_fit_line_lfr_consumes_cached_literature_measurements():
    from app.services.ai_tools import _exec_fit_line_lfr, store_search_results

    cache_key = "unit_line_lfr_cache"
    store_search_results(cache_key, {
        "schema_version": 1,
        "line_measurements": _measurement_rows(),
    })

    result = _exec_fit_line_lfr({"cache_key": cache_key, "line_id": "[CII]"}, "default")

    assert result["success"] is True
    assert result["publication_ready"] is True
    assert result["n_used"] == 6
    assert abs(result["beta"] - 0.5) < 1e-9
    assert abs(result["alpha"] - 8.0) < 1e-9
    assert result["citation_summary"]["citations"] == ["arXiv:2211.04968"]


def test_fit_line_lfr_partial_when_too_few_rows():
    from app.services.ai_tools import _exec_fit_line_lfr, store_search_results

    cache_key = "unit_line_lfr_tiny_cache"
    store_search_results(cache_key, {"line_measurements": _measurement_rows(3)})

    result = _exec_fit_line_lfr({"cache_key": cache_key, "min_rows": 5}, "default")

    assert result["success"] is True
    assert result["publication_ready"] is False
    assert result["__tool_status__"] == "PARTIAL"
    assert result["__do_not_claim__"] is True


def test_line_measurement_ready_cache_suppresses_synthetic_python():
    from app.api.chat import (
        _should_suppress_line_measurement_synthetic_python,
        _suppressed_line_measurement_python_result,
    )

    tool_call = {
        "name": "run_python",
        "input": {
            "data_source": "none_not_analyzing_real_data",
            "description": "fit [CII] L-FWHM relation",
            "code": "import pandas as pd\n# hardcoded ALPINE synthetic sample",
        },
    }

    assert _should_suppress_line_measurement_synthetic_python(
        tool_call,
        fit_ready_cache_keys=["latest_literature_tables:test"],
        latest_user_text="fit log L[CII] and FWHM",
        user_requested_synthetic_demo=False,
    )
    result = _suppressed_line_measurement_python_result(["latest_literature_tables:test"])
    assert result["__tool_status__"] == "EMPTY"
    assert "fit_line_lfr" in result["__message_to_model__"]


def test_run_python_sandbox_crash_detection_and_disable_threshold():
    from app.api.chat import (
        RUN_PYTHON_DISABLE_AFTER_CRASHES,
        _run_python_sandbox_crashed,
        _tool_disable_threshold,
    )

    malformed_empty_stderr = {
        "error": "run_python returned an empty response (tool response malformed; check backend logs)",
        "error_class": "SubprocessCrash",
        "stdout": "",
        "stderr": "",
    }
    non_crash_failure = {
        "success": False,
        "error": "NameError: name 'source_id' is not defined",
        "error_class": "runtime_error",
        "stderr": "Traceback...",
    }

    assert _run_python_sandbox_crashed(malformed_empty_stderr)
    assert not _run_python_sandbox_crashed(non_crash_failure)
    assert _tool_disable_threshold("run_python") == RUN_PYTHON_DISABLE_AFTER_CRASHES == 2
    assert _tool_disable_threshold("run_adql") == 3
