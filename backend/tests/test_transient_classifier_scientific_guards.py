"""Scientific-integrity guards for the synthetic transient classifier."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.ai_tools.stellar_tools import _exec_classify_transient
from app.services.result_provenance import normalize_tool_result
from app.services.transient_classifier import (
    MIN_LIGHT_CURVE_POINTS,
    TRANSIENT_CLASSES,
    TransientClassifier,
    extract_lc_features,
)


def _well_sampled_light_curve() -> tuple[list[float], list[float], list[float]]:
    times = np.linspace(0.0, 60.0, 30)
    magnitudes = 18.0 - 2.5 * np.exp(-((times - 15.0) ** 2) / 50.0)
    errors = np.full_like(times, 0.05)
    return times.tolist(), magnitudes.tolist(), errors.tolist()


def test_empty_feature_dict_fails_before_training(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_train(cls) -> None:  # pragma: no cover - failure sentinel
        raise AssertionError("invalid input must not train the synthetic model")

    monkeypatch.setattr(TransientClassifier, "_is_trained", False)
    monkeypatch.setattr(TransientClassifier, "_train_model", classmethod(_must_not_train))

    result = TransientClassifier.classify_transient({})

    assert result["success"] is False
    assert result["__tool_status__"] == "FAILED"
    assert result["__do_not_claim__"] is True
    assert result["classification"] == "Unknown"
    assert result["confidence"] == 0.0
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"features": []},
        {"times": [], "magnitudes": []},
        {
            "times": list(range(MIN_LIGHT_CURVE_POINTS - 1)),
            "magnitudes": [18.0] * (MIN_LIGHT_CURVE_POINTS - 1),
        },
        {"times": list(range(MIN_LIGHT_CURVE_POINTS)), "magnitudes": [18.0]},
    ],
)
async def test_empty_malformed_and_undersampled_tool_inputs_fail_closed(
    payload: dict,
) -> None:
    result = await _exec_classify_transient(payload)

    assert result["success"] is False
    assert result["__tool_status__"] == "FAILED"
    assert result["__do_not_claim__"] is True
    assert result["data_origin"] == "unavailable"
    assert result["analysis_status"] == "failed"
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False
    assert result["classification"] == "Unknown"
    assert result["confidence"] == 0.0


def test_partial_direct_features_do_not_trigger_median_imputed_prediction() -> None:
    result = TransientClassifier.classify_transient({"rise_time": 12.0})

    assert result["success"] is False
    assert "Missing or non-finite required" in result["error"]
    assert result["__do_not_claim__"] is True


@pytest.mark.asyncio
async def test_well_sampled_input_remains_explicitly_synthetic_and_nonclaimable() -> None:
    times, magnitudes, errors = _well_sampled_light_curve()

    result = await _exec_classify_transient(
        {
            "times": times,
            "magnitudes": magnitudes,
            "mag_errors": errors,
            "band": "r",
        }
    )

    assert result["classification"] in TRANSIENT_CLASSES
    assert 0.0 <= result["confidence"] <= 1.0
    assert result["__tool_status__"] == "SYNTHETIC"
    assert result["__do_not_claim__"] is True
    assert result["data_origin"] == "synthetic"
    assert result["analysis_status"] == "simulated_demo"
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False
    assert result["scientific_conclusion_ready"] is False
    assert result["classification_claimable"] is False
    assert result["confidence_calibrated"] is False
    assert result["model_provenance"]["training_data_origin"] == (
        "generated_feature_distributions"
    )
    assert "MUST NOT report" in result["__message_to_model__"]


def test_result_normalizer_cannot_launder_synthetic_classifier_as_real() -> None:
    result = normalize_tool_result(
        "classify_transient",
        {
            "success": True,
            "classification": "SN_Ia",
            "confidence": 0.999,
            "data_origin": "real_archive",
            "analysis_status": "completed",
            "publication_ready": True,
            "preliminary_ready": True,
            "__tool_status__": "COMPLETED",
        },
        tool_input={"features": {"rise_time": 18.0}},
    )

    assert list(result)[0] == "__tool_status__"
    assert result["__tool_status__"] == "SYNTHETIC"
    assert result["__do_not_claim__"] is True
    assert result["data_origin"] == "synthetic"
    assert result["analysis_status"] == "simulated_demo"
    assert result["publication_ready"] is False
    assert result["preliminary_ready"] is False
    assert result["classification_claimable"] is False
    assert result["confidence_calibrated"] is False
    assert "MUST NOT report" in result["__message_to_model__"]


def test_feature_extractor_marks_sparse_data_invalid() -> None:
    result = extract_lc_features([0.0, 1.0], [18.0, 17.5], [0.1, 0.1])

    assert result["feature_extraction_valid"] is False
    assert result["n_valid_points"] == 2
    assert f"At least {MIN_LIGHT_CURVE_POINTS}" in result["feature_extraction_error"]
