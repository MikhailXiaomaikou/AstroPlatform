from __future__ import annotations

import math


def test_radio_luminosity_uses_negative_alpha_k_correction_divisor():
    from app.connectors.radio import RadioAnalysis

    result = RadioAnalysis.radio_luminosity(
        flux_mJy=1.0,
        redshift=1.0,
        spectral_index=-0.7,
    )

    expected_k = (1.0 + 1.0) ** (1.0 - 0.7)
    assert math.isclose(result["k_correction_factor"], expected_k, rel_tol=1e-12)
    assert result["k_correction_applied"] == "divide_by_(1+z)^(1+alpha)"
    assert result["spectral_index_convention"] == "S_nu ∝ nu^alpha"
