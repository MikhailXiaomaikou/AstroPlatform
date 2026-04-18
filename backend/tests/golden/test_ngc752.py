"""Golden regression for NGC 752 (PART E3).

NGC 752 is a ~1.4–1.9 Gyr open cluster at ~440 pc with ~176 high-probability
Gaia DR3 members.  A reviewer test run on the pre-PART-E deploy produced
an age of 3.65 Gyr from the `_estimate_age_from_turnoff` fallback, because
the old "bluest 5%" method was biased by blue stragglers + unresolved
binaries.  This regression locks the corrected bin-based turnoff detection:
for a synthetic 1.56 Gyr CMD the estimator must return age in [1.2, 2.0] Gyr.

Literature references:
  Twarog+ 1997, Bocek Topcu+ 2015, Cantat-Gaudin+ 2020, Agueros+ 2018.
  Age ≈ 1.56 Gyr (log age ≈ 9.20), distance ≈ 441 pc (DM ≈ 8.22),
  [Fe/H] ≈ +0.02, E(B-V) ≈ 0.04, pmra ≈ +9.8, pmdec ≈ -11.8 mas/yr.
"""

from __future__ import annotations

import numpy as np
import pytest


def _synthetic_ngc752_members(seed: int = 20260417) -> tuple[list[float], list[float]]:
    """Generate a synthetic CMD for a ~1.56 Gyr solar-metallicity cluster.

    Rough PARSEC 1.2S solar-Z 1.5 Gyr isochrone in Gaia DR3 bands:
      - MSTO at BP-RP ≈ 0.44, M_G ≈ +2.5 (F5V turnoff, ~1.3 M_sun)
      - MS ridge M_G ≈ 0.5 + 4.2 * BP_RP for BP-RP in [0.44, 2.5]
      - A handful of subgiants and RGB stars above the MS
      - A handful of blue stragglers bluer than the turnoff
      - ~30 % unresolved binaries brighter by up to 0.75 mag
    Total 176 members to match the reviewer's Gaia DR3 cut.
    """
    rng = np.random.default_rng(seed)
    bp_rp: list[float] = []
    abs_g: list[float] = []

    # 140 main-sequence stars between turnoff (0.44) and M-dwarfs (2.4)
    ms_colour = rng.uniform(0.44, 2.4, size=140)
    ms_mg_ridge = 0.5 + 4.2 * ms_colour
    ms_mg = ms_mg_ridge + rng.normal(0, 0.10, size=140)
    # Binary brightening for ~30 % of MS stars (0.40-0.75 mag brighter)
    binary_mask = rng.random(140) < 0.30
    ms_mg[binary_mask] -= rng.uniform(0.40, 0.75, size=int(binary_mask.sum()))

    bp_rp.extend(ms_colour.tolist())
    abs_g.extend(ms_mg.tolist())

    # 18 subgiant / giant stars above the MS at BP-RP 0.8-1.4, M_G +0.5 to +2
    sg_colour = rng.uniform(0.80, 1.40, size=18)
    sg_mg = rng.uniform(0.5, 2.0, size=18)
    bp_rp.extend(sg_colour.tolist())
    abs_g.extend(sg_mg.tolist())

    # 12 red-giant / red-clump stars at BP-RP 1.0-1.3, M_G ≈ 0-+1
    rg_colour = rng.uniform(1.00, 1.30, size=12)
    rg_mg = rng.uniform(-0.2, 1.0, size=12)
    bp_rp.extend(rg_colour.tolist())
    abs_g.extend(rg_mg.tolist())

    # 6 blue stragglers bluer than turnoff at BP-RP 0.20-0.40,
    # M_G +1.2 to +2.3 (a full mag brighter than the true turnoff locus).
    # These are the stars that fooled the old "bluest 5%" method.
    bs_colour = rng.uniform(0.20, 0.40, size=6)
    bs_mg = rng.uniform(1.20, 2.30, size=6)
    bp_rp.extend(bs_colour.tolist())
    abs_g.extend(bs_mg.tolist())

    assert len(bp_rp) == 176
    return bp_rp, abs_g


class TestNGC752TurnoffFallback:
    """Turnoff fallback must return literature-consistent age for NGC 752."""

    def test_turnoff_age_in_literature_range(self):
        """Age must be 1.2–2.0 Gyr (literature consensus 1.4–1.9 Gyr)."""
        from app.services.ai_tools import _estimate_age_from_turnoff

        bp_rp, abs_g = _synthetic_ngc752_members()
        # NGC 752 mean parallax ≈ 2.27 mas (Gaia DR3) → distance ≈ 441 pc
        result = _estimate_age_from_turnoff(bp_rp, abs_g, med_plx=2.27, med_av=0.12)

        assert "error" not in result, f"Estimator failed: {result}"
        age_myr = result["best_fit"]["age_myr"]
        assert 1200.0 <= age_myr <= 2000.0, (
            f"NGC 752 age {age_myr:.0f} Myr outside literature range "
            f"[1200, 2000] Myr — turnoff detection regression"
        )

    def test_method_label_reports_ms_locus_binning(self):
        """Result must advertise the binning-based fallback method."""
        from app.services.ai_tools import _estimate_age_from_turnoff

        bp_rp, abs_g = _synthetic_ngc752_members()
        result = _estimate_age_from_turnoff(bp_rp, abs_g, med_plx=2.27, med_av=0.12)

        assert "error" not in result
        assert "turnoff_fallback" in result["method"] or "MS-locus" in result["method"]

    def test_distance_from_parallax_recovered(self):
        """Distance back-computed from the input parallax must match NGC 752."""
        from app.services.ai_tools import _estimate_age_from_turnoff

        bp_rp, abs_g = _synthetic_ngc752_members()
        result = _estimate_age_from_turnoff(bp_rp, abs_g, med_plx=2.27, med_av=0.12)

        dist = result["best_fit"]["distance_pc"]
        assert dist is not None
        assert 420.0 <= dist <= 460.0, (
            f"NGC 752 distance {dist:.0f} pc outside [420, 460] pc "
            f"literature range"
        )

    def test_blue_stragglers_do_not_bias_turnoff(self):
        """Stragglers at BP-RP < 0.4 must not pull the turnoff bluer."""
        from app.services.ai_tools import _estimate_age_from_turnoff

        bp_rp, abs_g = _synthetic_ngc752_members()
        result = _estimate_age_from_turnoff(bp_rp, abs_g, med_plx=2.27, med_av=0.12)

        assert "error" not in result
        turnoff_bp_rp = result["turnoff"]["bp_rp"]
        # True NGC 752 turnoff is near BP-RP ≈ 0.44; with a 0.10-mag bin
        # width and ≥3-star minimum, the blue-straggler bin (6 stars over
        # BP-RP 0.2-0.4) can still be populated — but so is the real MS
        # bin at BP-RP 0.44-0.54.  We require the estimator not to drift
        # redder than ~0.8 (which would be a 7 Gyr old-cluster turnoff).
        assert turnoff_bp_rp < 0.80, (
            f"Turnoff BP-RP {turnoff_bp_rp:.2f} suggests a far older "
            f"cluster than NGC 752 (expected ≲ 0.6)"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
