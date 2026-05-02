from __future__ import annotations


def _rows() -> list[dict]:
    return [
        {
            "source_name": "HZ1",
            "line_id": "[CII]",
            "redshift": 5.7,
            "log_luminosity": 8.4,
            "fwhm_km_s": 180,
            "citation": {"arxiv_id": "2002.00962", "table_label": "Table A1"},
        },
        {
            "source_name": "HZ2",
            "line_id": "[CII] 158um",
            "redshift": 5.8,
            "log_luminosity": 8.6,
            "fwhm_km_s": 210,
            "citation": {"arxiv_id": "2002.00962", "table_label": "Table A1"},
        },
        {
            "source_name": "BAD",
            "line_id": "[CII]",
            "redshift": 5.9,
            "log_luminosity": 8.2,
            "citation": {"arxiv_id": "2002.00962"},
        },
    ]


def test_prepare_spectral_measurements_validates_fit_ready_rows() -> None:
    from app.services.spectral_measurement_workbench import prepare_spectral_measurements

    result = prepare_spectral_measurements(_rows(), line_id="CII", min_fit_rows=2)

    assert result["success"] is True
    assert result["fit_ready"] is True
    assert result["n_fit_ready_rows"] == 2
    assert result["line_inventory"]["[CII] 158um"] == 3
    assert result["range_summary"]["redshift"]["min"] == 5.7
    assert result["range_summary"]["fwhm_km_s"]["max"] == 210
    assert result["rejected_preview"][0]["missing"] == ["fwhm_km_s"]


def test_prepare_spectral_measurements_tool_reads_cache() -> None:
    from app.services.ai_tools import _exec_prepare_spectral_measurements, store_search_results

    cache_key = "unit_spectral_workbench"
    store_search_results(cache_key, {"line_measurements": _rows()})

    result = _exec_prepare_spectral_measurements(
        {"cache_key": cache_key, "line_id": "[CII]", "min_fit_rows": 2},
        "default",
    )

    assert result["cache_key"] == cache_key
    assert result["fit_ready"] is True
    assert result["supports_measurement_claims"] is True


def test_prepare_spectral_measurements_tool_marks_partial_when_not_fit_ready() -> None:
    from app.services.ai_tools import _exec_prepare_spectral_measurements, store_search_results

    cache_key = "unit_spectral_workbench_partial"
    store_search_results(cache_key, {"line_measurements": _rows()[:1]})

    result = _exec_prepare_spectral_measurements({"cache_key": cache_key, "min_fit_rows": 5}, "default")

    assert result["__tool_status__"] == "PARTIAL"
    assert result["__do_not_claim__"] is True
    assert result["fit_ready"] is False
