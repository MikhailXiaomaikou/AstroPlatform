"""Numerical-equivalence regressions for the optimized photo-z fitter."""

import numpy as np
import pytest

import app.services.photo_z_pro as photo_z_pro_module
from app.services.photo_z_pro import (
    _add_emission_lines,
    _build_model_magnitude_grid,
    _calzetti_attenuation,
    _generate_sed_templates,
    _make_redshift_grid,
    _madau_igm,
    _synthetic_mag,
    fit_template_enhanced,
)


@pytest.mark.parametrize(
    ("include_emission_lines", "include_igm"),
    [(True, True), (True, False), (False, True), (False, False)],
)
def test_vectorized_grid_matches_scalar_synthetic_photometry(
    include_emission_lines: bool,
    include_igm: bool,
) -> None:
    """The optimized projection must reproduce the original cell-by-cell math."""
    templates = _generate_sed_templates()[:2]
    ebv_grid = [0.0, 0.2]
    z_grid = np.array(
        [0.0, 0.1, np.nextafter(0.1, np.inf), 0.11, 0.65, 2.3, 5.99]
    )
    bands = ["u", "g", "r", "i", "z"]

    actual, metadata = _build_model_magnitude_grid(
        templates,
        ebv_grid,
        z_grid,
        bands,
        include_emission_lines=include_emission_lines,
        include_igm=include_igm,
    )

    expected = []
    expected_metadata = []
    for template in templates:
        for ebv in ebv_grid:
            expected_metadata.append((template["name"], ebv))
            magnitudes_at_z = []
            for z in z_grid:
                wave_obs = template["wavelength"] * (1 + z)
                flux = template["flux"].copy()
                if ebv > 0:
                    flux *= _calzetti_attenuation(template["wavelength"], ebv)
                if include_igm and z > 0.1:
                    flux *= _madau_igm(wave_obs, z)
                if include_emission_lines:
                    flux = _add_emission_lines(wave_obs, flux, z)
                magnitudes_at_z.append(
                    [_synthetic_mag(wave_obs, flux, band) for band in bands]
                )
            expected.append(magnitudes_at_z)

    assert metadata == expected_metadata
    np.testing.assert_allclose(actual, np.asarray(expected), rtol=1e-12, atol=1e-12)


@pytest.mark.timeout(10)
def test_default_grid_preserves_reference_science_result() -> None:
    """Keep the full 30 x 8 x 601 science grid and its pre-optimization result."""
    result = fit_template_enhanced(
        {"u": 23.0, "g": 22.1, "r": 21.5, "i": 20.8, "z": 20.3},
        {"u": 0.1, "g": 0.05, "r": 0.04, "i": 0.04, "z": 0.05},
    )

    assert result["n_templates"] == 30
    assert result["ebv_grid"] == [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5]
    assert len(result["pz_grid"]) == 601
    assert result["z_phot"] == 0.66
    assert result["best_z_ml"] == 0.65
    assert result["best_template"] == "AGN_red"
    assert result["best_ebv"] == 0.5
    assert result["z_68_lo"] == 0.51
    assert result["z_68_hi"] == 0.84
    assert result["chi2_reduced"] == pytest.approx(3.1976647100293154, abs=1e-12)

    reference_pz = {
        0: 0.0748171780501318,
        30: 0.27538206269637483,
        50: 1.040146753788173,
        66: 3.154385755957503,
        84: 0.7416673312857145,
        100: 0.054780825141298485,
        150: 9.639864175933113e-09,
        300: 0.004008316024139651,
    }
    for index, expected in reference_pz.items():
        assert result["pz_values"][index] == pytest.approx(expected, rel=1e-10, abs=1e-12)


def test_invalid_or_unknown_bands_do_not_change_valid_band_fit() -> None:
    """Projection only includes the measurements that contribute to chi-square."""
    clean = fit_template_enhanced(
        {"g": 22.1, "r": 21.5},
        {"g": 0.05, "r": 0.04},
        z_range=(0.0, 0.3),
    )
    with_ignored_bands = fit_template_enhanced(
        {"not_a_filter": 12.0, "g": 22.1, "r": 21.5, "i": np.nan},
        {"not_a_filter": 0.01, "g": 0.05, "r": 0.04, "i": 0.04},
        z_range=(0.0, 0.3),
    )

    assert with_ignored_bands["n_bands"] == 2
    assert with_ignored_bands["best_template"] == clean["best_template"]
    assert with_ignored_bands["best_ebv"] == clean["best_ebv"]
    assert with_ignored_bands["z_phot"] == clean["z_phot"]
    np.testing.assert_allclose(
        with_ignored_bands["pz_values"],
        clean["pz_values"],
        rtol=0,
        atol=0,
    )


def test_zero_filter_coverage_is_explicitly_invalid_at_high_redshift() -> None:
    """An underflowed FUV response must not become a finite magnitude of 99."""
    template = _generate_sed_templates()[0]
    z_grid = np.array([0.0, 5.58, 6.0])
    model_mags, _ = _build_model_magnitude_grid(
        [template],
        [0.0],
        z_grid,
        ["FUV"],
        include_emission_lines=False,
        include_igm=False,
        z_chunk_size=1,
    )

    assert np.isfinite(model_mags[0, 0, 0])
    assert np.isnan(model_mags[0, 1:, 0]).all()
    for z in z_grid[1:]:
        wave_obs = template["wavelength"] * (1 + z)
        assert np.isnan(_synthetic_mag(wave_obs, template["flux"], "FUV"))


def test_incomplete_filter_coverage_rejects_model_cells_without_nan_pz() -> None:
    """Finite low-z cells survive while uncovered high-z cells get zero weight."""
    result = fit_template_enhanced(
        {"FUV": 23.0, "NUV": 22.5},
        {"FUV": 0.1, "NUV": 0.1},
        z_range=(0.0, 6.0),
        z_step=0.5,
        ebv_grid=[0.0],
        include_emission_lines=False,
    )

    pz = np.asarray(result["pz_values"])
    assert np.isfinite(pz).all()
    assert pz[-1] == 0.0


def test_templates_must_share_the_exact_wavelength_grid() -> None:
    templates = _generate_sed_templates()[:2]
    templates[1] = {**templates[1], "wavelength": templates[1]["wavelength"].copy()}
    templates[1]["wavelength"][10] += 1e-9

    with pytest.raises(ValueError, match="exact wavelength grid"):
        _build_model_magnitude_grid(
            templates,
            [0.0],
            np.array([0.0]),
            ["g", "r"],
            include_emission_lines=False,
            include_igm=False,
        )


def test_redshift_grid_respects_both_bounds_without_overshoot() -> None:
    non_divisible = _make_redshift_grid((0.0, 1.0), 0.3)
    exact_decimal = _make_redshift_grid((0.0, 0.3), 0.1)

    np.testing.assert_allclose(non_divisible, [0.0, 0.3, 0.6, 0.9])
    assert non_divisible[0] == 0.0
    assert non_divisible[-1] <= 1.0
    assert exact_decimal[-1] == 0.3
    with pytest.raises(ValueError, match="positive"):
        _make_redshift_grid((0.0, 1.0), 0.0)
    with pytest.raises(ValueError, match="z_range"):
        _make_redshift_grid((-0.1, 1.0), 0.1)


@pytest.mark.parametrize(
    ("i_mag", "i_error"),
    [
        (np.nan, 0.1),
        (99.0, 0.1),
        (20.8, np.nan),
        (20.8, np.inf),
    ],
)
def test_invalid_i_measurement_skips_magnitude_prior_without_nan(
    i_mag: float,
    i_error: float,
) -> None:
    result = fit_template_enhanced(
        {"g": 22.1, "r": 21.5, "i": i_mag},
        {"g": 0.05, "r": 0.04, "i": i_error},
        z_range=(0.0, 0.2),
        prior="magnitude",
    )

    assert result["prior"] == "magnitude"
    assert result["prior_applied"] is False
    assert result["n_bands"] == 2
    assert np.isfinite(result["pz_values"]).all()


def test_valid_magnitude_prior_is_finite_and_reported_as_applied() -> None:
    result = fit_template_enhanced(
        {"g": 22.1, "r": 21.5, "i": 20.8},
        {"g": 0.05, "r": 0.04, "i": 0.04},
        z_range=(0.0, 0.2),
        prior="magnitude",
    )

    assert result["prior_applied"] is True
    assert np.isfinite(result["pz_values"]).all()


def test_equal_chi2_uses_original_template_then_lower_redshift_tie_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(photo_z_pro_module, "_generate_sed_templates", lambda: [{}, {}])
    monkeypatch.setattr(
        photo_z_pro_module,
        "_prepare_model_fluxes",
        lambda templates, ebv_grid: (
            np.array([1.0, 2.0]),
            np.ones((2, 2)),
            [("first", 0.0), ("second", 0.0)],
        ),
    )

    def equal_model_chunks(*args, **kwargs):
        z_grid = args[2]
        yield 0, len(z_grid), np.zeros((2, len(z_grid), 2))

    monkeypatch.setattr(
        photo_z_pro_module,
        "_iter_model_magnitude_chunks",
        equal_model_chunks,
    )
    result = fit_template_enhanced(
        {"g": 0.0, "r": 0.0},
        {"g": 1.0, "r": 1.0},
        z_range=(0.0, 0.1),
        z_step=0.1,
        ebv_grid=[0.0],
    )

    assert result["best_template"] == "first"
    assert result["best_z_ml"] == 0.0
    assert result["z_phot"] == 0.0


def test_fine_redshift_grid_projects_only_bounded_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk_lengths: list[int] = []
    original = photo_z_pro_module._model_magnitude_chunk

    def record_chunk(*args, **kwargs):
        chunk_lengths.append(len(args[2]))
        return original(*args, **kwargs)

    monkeypatch.setattr(photo_z_pro_module, "_MODEL_Z_CHUNK_SIZE", 3)
    monkeypatch.setattr(photo_z_pro_module, "_model_magnitude_chunk", record_chunk)
    result = fit_template_enhanced(
        {"g": 22.1, "r": 21.5},
        {"g": 0.05, "r": 0.04},
        z_range=(0.0, 0.02),
        z_step=0.001,
        ebv_grid=[0.0],
        include_emission_lines=False,
    )

    assert len(result["pz_grid"]) == 21
    assert len(chunk_lengths) > 1
    assert max(chunk_lengths) <= 3
    assert np.isfinite(result["pz_values"]).all()
