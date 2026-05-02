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


# ── Hardcoded-cosmology fix (5-2 sweep) ───────────────────────────────


def test_radio_luminosity_default_cosmology_is_platform_planck18_not_wmap5() -> None:
    """以前 radio_luminosity 内嵌 H0=70, Om0=0.3 (WMAP5/7 era 残留),
    跟平台默认 Planck18 (H0=67.36, Om0=0.3153) 不一致, 同 z 算出的
    radio luminosity 偏 ~+4%. 修后默认应走平台 preset."""
    from app.connectors.radio import RadioAnalysis

    result = RadioAnalysis.radio_luminosity(flux_mJy=1.0, redshift=1.0)
    cosmo = result["cosmology"]
    # name 必须显式声明, 不能是模糊 "legacy_radio_tool_default"
    assert cosmo["name"] in {"planck18", "Planck18"}
    # 数值必须是 Planck18 (Planck Collab. 2020 A&A 641 A6)
    assert math.isclose(cosmo["H0"], 67.36, abs_tol=0.01)
    assert math.isclose(cosmo["Om0"], 0.3153, abs_tol=0.001)
    # bibcode 必须有, 让 paper draft 可引
    assert cosmo["bibcode"] == "2020A&A...641A...6P"


def test_radio_luminosity_explicit_cosmology_preset_overrides_default() -> None:
    """传 cosmology="riess22_shoes" → result.cosmology 必须指 Riess+22
    H0=73.04, 不是 Planck18. 同 z 下 dl 应该明显小 (H0 大 → dl 小)."""
    from app.connectors.radio import RadioAnalysis

    out_planck = RadioAnalysis.radio_luminosity(flux_mJy=10.0, redshift=2.0)
    out_riess = RadioAnalysis.radio_luminosity(
        flux_mJy=10.0, redshift=2.0, cosmology="riess22_shoes",
    )

    assert out_planck["cosmology"]["name"] in {"planck18", "Planck18"}
    assert out_riess["cosmology"]["name"] == "riess22_shoes"
    assert math.isclose(out_riess["cosmology"]["H0"], 73.04, abs_tol=0.01)
    # H0 大 → dl 小 → luminosity 小 (固定 flux)
    assert out_riess["luminosity_W_Hz"] < out_planck["luminosity_W_Hz"]
    # 偏移 ~ (67.36/73.04)^2 ≈ 0.85, 即 luminosity 比 Planck18 偏小 ~15%
    ratio = out_riess["luminosity_W_Hz"] / out_planck["luminosity_W_Hz"]
    assert 0.80 < ratio < 0.90, (
        f"Riess22 vs Planck18 luminosity ratio expected ~0.85, got {ratio:.3f}"
    )


def test_radio_luminosity_cosmology_provenance_complete() -> None:
    """result.cosmology 必须含 H0/Om0/bibcode/name/source 全字段, 让
    paper_generator + claim_validator 拿到真 provenance, 不是 magic 70/0.3."""
    from app.connectors.radio import RadioAnalysis

    result = RadioAnalysis.radio_luminosity(flux_mJy=5.0, redshift=0.5)
    cosmo = result["cosmology"]
    for key in ("name", "H0", "Om0", "bibcode", "source"):
        assert key in cosmo, f"radio cosmology provenance missing key {key!r}"
    # source 不能再是老 "legacy_radio_tool_default" 字面量
    assert "legacy" not in str(cosmo["source"]).lower()
    assert "platform_preset" in str(cosmo["source"])
