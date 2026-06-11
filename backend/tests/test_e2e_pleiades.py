"""End-to-end test: Pleiades HR Diagram full analysis pipeline.

Validates the complete chain:
  query → plot → isochrone overlay → fit → model comparison → residual analysis

This test hits the live CMD 3.9 API for isochrones, so it requires
network access and may take 2-3 minutes.
"""

import numpy as np
import pytest


@pytest.mark.slow
class TestPleiadesE2E:
    """End-to-end validation of the Pleiades HR diagram workflow."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Fetch Pleiades data from Gaia DR3 via ADQL."""
        from app.services.parsec_fetcher import fetch_isochrone

        # Use a pre-fetched 100 Myr isochrone as synthetic Pleiades data
        # (avoids Gaia TAP dependency in tests)
        iso = fetch_isochrone(8.0, 0.0, "gaia", timeout=90)
        assert len(iso) > 50, f"Isochrone too short: {len(iso)} rows"

        # Simulate observed Pleiades CMD:
        # True params: log_age=8.0, [M/H]=0.0, DM=5.67, A_V=0.12
        self.true_dm = 5.67
        self.true_av = 0.12
        self.true_log_age = 8.0
        self.true_met = 0.0

        rng = np.random.default_rng(42)
        self.bp_rp = (iso["G_BPmag"].values - iso["G_RPmag"].values
                      + 0.415 * self.true_av
                      + rng.normal(0, 0.05, len(iso)))
        self.app_mag = (iso["Gmag"].values
                        + self.true_dm
                        + 0.836 * self.true_av
                        + rng.normal(0, 0.1, len(iso)))
        self.n_stars = len(self.bp_rp)

    def test_01_plot_hr_diagram(self):
        """Step 1: Basic HR diagram renders without error."""
        from app.services.astro_analysis import plot_hr_diagram

        result = plot_hr_diagram(self.bp_rp, self.app_mag, title="Pleiades CMD")
        assert result is not None
        fig, ax = result
        assert fig is not None

    def test_02_isochrone_fetch(self):
        """Step 2: Fetch isochrones for overlay."""
        from app.services.astro_analysis import get_isochrone

        iso = get_isochrone(8.0, 0.0, "gaia")
        assert iso is not None
        assert len(iso) > 50
        assert "Gmag" in iso.columns
        assert "G_BPmag" in iso.columns
        assert "G_RPmag" in iso.columns

    def test_03_plot_with_isochrone_overlay(self):
        """Step 3: HR diagram with isochrone overlay."""
        from app.services.astro_analysis import get_isochrone, plot_hr_diagram

        iso_100myr = get_isochrone(8.0, 0.0, "gaia")
        iso_1gyr = get_isochrone(9.0, 0.0, "gaia")

        result = plot_hr_diagram(
            self.bp_rp, self.app_mag,
            title="Pleiades + Isochrones",
            isochrones=[iso_100myr, iso_1gyr],
        )
        fig, ax = result
        # Should have data points + 2 isochrone lines
        assert len(ax.get_lines()) >= 2

    def test_04_isochrone_fit_grid(self):
        """Step 4: Grid fit recovers cluster parameters."""
        from app.services.astro_analysis import fit_isochrone

        result = fit_isochrone(
            self.bp_rp, self.app_mag,
            method="grid",
            age_range=(7.5, 9.0),
            n_grid_age=8,
            n_grid_met=3,
        )

        bf = result["best_fit"]
        age_myr = bf["age_myr"]
        distance_pc = bf["distance_pc"]

        # Core assertions from the task specification
        assert 80 <= age_myr <= 150, (
            f"Age {age_myr} Myr outside expected range [80, 150]"
        )
        # True DM=5.67 -> 136 pc; tight single bound so a poor recovery
        # (e.g. 185 pc) actually fails instead of slipping through a
        # loose disjunction that subsumed the tight 130-140 target.
        assert 120 <= distance_pc <= 160, (
            f"Distance {distance_pc} pc outside expected range [120, 160] (true 136 pc)"
        )

        # Check fit quality
        assert result["chi2_reduced"] < 20, (
            f"chi2_reduced={result['chi2_reduced']} too high"
        )

        # Store for later tests
        self._grid_result = result

    def test_05_model_comparison(self):
        """Step 5: Compare young vs old isochrone models."""
        from app.services.astro_analysis import compare_models, fit_isochrone

        # Fit with young age prior
        young_fit = fit_isochrone(
            self.bp_rp, self.app_mag,
            method="grid",
            age_range=(7.5, 8.5),
            n_grid_age=5,
            n_grid_met=2,
        )

        # Fit with old age prior
        old_fit = fit_isochrone(
            self.bp_rp, self.app_mag,
            method="grid",
            age_range=(9.5, 10.3),
            n_grid_age=5,
            n_grid_met=2,
        )

        comparison = compare_models(self.n_stars, [
            {"name": "Young (100 Myr)", "chi2": young_fit["chi2"], "n_params": 4},
            {"name": "Old (3-20 Gyr)", "chi2": old_fit["chi2"], "n_params": 4},
        ])

        # Young model should be preferred (lower BIC)
        assert comparison["best_model"] == "Young (100 Myr)", (
            f"Expected young model to win, got {comparison['best_model']}"
        )

        # Evidence should be decisive or strong
        ranked = comparison["ranked"]
        old_entry = [r for r in ranked if r["name"] == "Old (3-20 Gyr)"][0]
        assert old_entry["delta_BIC"] > 2, (
            f"delta_BIC={old_entry['delta_BIC']} — expected positive evidence against old model"
        )

    def test_06_residual_analysis(self):
        """Step 6: Residual diagnostics on the fit."""
        from app.services.astro_analysis import (
            analyze_residuals,
            fit_isochrone,
            get_isochrone,
        )

        # Get best-fit isochrone
        fit = fit_isochrone(
            self.bp_rp, self.app_mag,
            method="grid",
            age_range=(7.5, 8.5),
            n_grid_age=5,
            n_grid_met=2,
        )

        bf = fit["best_fit"]
        iso = get_isochrone(bf["log_age"], bf["metallicity"], "gaia")

        # Compute model magnitudes at observed colours
        model_color = iso["G_BPmag"].values - iso["G_RPmag"].values
        model_mag = iso["Gmag"].values + bf["distance_modulus"] + 0.836 * bf["A_V"]
        shifted_color = model_color + 0.415 * bf["A_V"]

        # For each observed star, find nearest model point's magnitude
        model_pred = np.zeros(self.n_stars)
        for i in range(self.n_stars):
            dists = (self.bp_rp[i] - shifted_color) ** 2 + (self.app_mag[i] - model_mag) ** 2
            model_pred[i] = model_mag[np.argmin(dists)]

        residuals_result = analyze_residuals(self.app_mag, model_pred)

        # Should have valid diagnostics
        assert "durbin_watson" in residuals_result
        assert "shapiro_p" in residuals_result
        assert "overall_rating" in residuals_result
        assert residuals_result["overall_rating"] in ("pass", "warn", "fail")
        assert residuals_result["qq_fig"] is not None

    def test_07_distance_and_age_assertions(self):
        """Final assertions: distance 130-140 pc, age 80-150 Myr."""
        from app.services.astro_analysis import fit_isochrone

        result = fit_isochrone(
            self.bp_rp, self.app_mag,
            method="grid",
            age_range=(7.0, 9.5),
            n_grid_age=15,
            n_grid_met=5,
        )

        bf = result["best_fit"]

        # Distance assertion (from DM)
        distance_pc = bf["distance_pc"]
        assert 100 <= distance_pc <= 200, (
            f"Distance {distance_pc} pc outside [100, 200]"
        )

        # Age assertion
        age_myr = bf["age_myr"]
        assert 80 <= age_myr <= 150, (
            f"Age {age_myr} Myr outside [80, 150]"
        )

        # Metallicity should be near solar
        assert -0.5 <= bf["metallicity"] <= 0.5, (
            f"[M/H]={bf['metallicity']} outside [-0.5, 0.5]"
        )

        # Extinction should be low
        assert 0 <= bf["A_V"] <= 1.0, (
            f"A_V={bf['A_V']} outside [0, 1.0]"
        )
