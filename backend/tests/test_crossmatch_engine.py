"""Tests for the CrossMatchEngine with known mock catalogs.

Constructs two catalogs with deterministic positions so that match/no-match
membership is exactly known.  All six join types are verified.
"""

import asyncio
import math

import numpy as np
import pandas as pd
import pytest

from app.services.crossmatch_engine import CrossMatchEngine


# ── Test fixtures ──

# Catalog 1: five sources at known positions
# Sources A-E spread along RA at dec=+10
CAT1 = pd.DataFrame(
    {
        "ra": [10.0, 20.0, 30.0, 40.0, 50.0],
        "dec": [10.0, 10.0, 10.0, 10.0, 10.0],
        "id1": ["A", "B", "C", "D", "E"],
    }
)

# Catalog 2: four sources
# - Source P at (10.0001, 10.0) -> matches A (~0.36 arcsec at dec=10)
# - Source Q at (20.0002, 10.0) -> matches B (~0.72 arcsec at dec=10)
# - Source R at (30.01, 10.0)   -> NO match to C (~35 arcsec, outside 3")
# - Source S at (60.0, 10.0)    -> NO match to anything in cat1
CAT2 = pd.DataFrame(
    {
        "ra": [10.0001, 20.0002, 30.01, 60.0],
        "dec": [10.0, 10.0, 10.0, 10.0],
        "id2": ["P", "Q", "R", "S"],
    }
)

# Expected matches at 3" radius: A-P, B-Q
# Unmatched in cat1: C, D, E
# Unmatched in cat2: R, S

RADIUS = 3.0  # arcsec


@pytest.fixture
def engine():
    return CrossMatchEngine()


# ── Helpers ──

def run(coro):
    """Run an async coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sep_arcsec(ra1, dec1, ra2, dec2):
    """Quick spherical-cosine separation in arcsec."""
    d2r = math.pi / 180.0
    cos_d = (
        math.sin(dec1 * d2r) * math.sin(dec2 * d2r)
        + math.cos(dec1 * d2r) * math.cos(dec2 * d2r) * math.cos((ra1 - ra2) * d2r)
    )
    cos_d = max(-1.0, min(1.0, cos_d))
    return math.acos(cos_d) * 180.0 / math.pi * 3600.0


# ── Tests: find="best" (nearest-match) ──


class TestBestMatch1and2:
    """Inner join with best match: only matched rows."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="best"))
        assert len(result) == 2

    def test_matched_ids(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="best"))
        assert set(result["id1"]) == {"A", "B"}
        assert set(result["id2_2"]) == {"P", "Q"}

    def test_separation_column(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="best"))
        assert "separation_arcsec" in result.columns
        assert all(result["separation_arcsec"] <= RADIUS)
        assert all(result["separation_arcsec"] > 0)


class TestBestMatchAll1:
    """Left join: all rows from table1, matched table2 or NaN."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all1", find="best"))
        assert len(result) == len(CAT1)  # 5

    def test_matched_rows_have_t2_data(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all1", find="best"))
        matched = result[result["separation_arcsec"].notna()]
        assert len(matched) == 2
        assert set(matched["id1"]) == {"A", "B"}

    def test_unmatched_rows_have_nan(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all1", find="best"))
        unmatched = result[result["separation_arcsec"].isna()]
        assert len(unmatched) == 3
        assert set(unmatched["id1"]) == {"C", "D", "E"}
        assert all(unmatched["ra_2"].isna())


class TestBestMatchAll2:
    """Right join: all rows from table2, matched table1 or NaN."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all2", find="best"))
        assert len(result) == len(CAT2)  # 4

    def test_matched_rows(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all2", find="best"))
        matched = result[result["separation_arcsec"].notna()]
        assert len(matched) == 2
        assert set(matched["id2_2"]) == {"P", "Q"}

    def test_unmatched_rows(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="all2", find="best"))
        unmatched = result[result["separation_arcsec"].isna()]
        assert len(unmatched) == 2
        assert set(unmatched["id2_2"]) == {"R", "S"}


class TestBestMatch1or2:
    """Full outer join: matched rows + unmatched from both tables."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1or2", find="best"))
        # 2 matched + 3 unmatched t1 + 2 unmatched t2 = 7
        assert len(result) == 7

    def test_all_sources_present(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1or2", find="best"))
        # All t1 ids present (matched + unmatched)
        t1_ids = set(result["id1"].dropna())
        assert t1_ids == {"A", "B", "C", "D", "E"}
        # All t2 ids present
        t2_ids = set(result["id2_2"].dropna())
        assert t2_ids == {"P", "Q", "R", "S"}


class TestBestMatch1not2:
    """Anti-join: rows in table1 with NO match in table2."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1not2", find="best"))
        assert len(result) == 3

    def test_only_unmatched_ids(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1not2", find="best"))
        assert set(result["id1"]) == {"C", "D", "E"}

    def test_no_t2_columns(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1not2", find="best"))
        # Should not have t2 data columns
        assert "id2_2" not in result.columns
        assert "ra_2" not in result.columns


class TestBestMatch2not1:
    """Anti-join: rows in table2 with NO match in table1."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="2not1", find="best"))
        assert len(result) == 2

    def test_only_unmatched_ids(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="2not1", find="best"))
        assert set(result["id2_2"]) == {"R", "S"}


# ── Tests: find="all" (all pairs within radius) ──


class TestAllMatch1and2:
    """Inner join with all matches."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="all"))
        # A-P and B-Q are the only pairs within 3"
        assert len(result) == 2

    def test_separation_values(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="all"))
        for _, row in result.iterrows():
            expected = _sep_arcsec(row["ra"], row["dec"], row["ra_2"], row["dec_2"])
            assert abs(row["separation_arcsec"] - expected) < 0.1


class TestAllMatch1not2:
    """Anti-join with find=all."""

    def test_row_count(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1not2", find="all"))
        assert len(result) == 3

    def test_correct_ids(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1not2", find="all"))
        assert set(result["id1"]) == {"C", "D", "E"}


class TestAllMatch1or2:
    """Full outer join with find=all."""

    def test_contains_all_sources(self, engine):
        result = run(engine.crossmatch(CAT1, CAT2, RADIUS, join="1or2", find="all"))
        t1_ids = set(result["id1"].dropna())
        t2_ids = set(result["id2_2"].dropna())
        assert t1_ids == {"A", "B", "C", "D", "E"}
        assert t2_ids == {"P", "Q", "R", "S"}


# ── Tests: edge cases ──


class TestEdgeCases:
    """Edge cases and validation."""

    def test_missing_ra_raises(self, engine):
        bad = pd.DataFrame({"x": [1.0], "dec": [10.0]})
        with pytest.raises(ValueError, match="ra"):
            run(engine.crossmatch(bad, CAT2, RADIUS))

    def test_missing_dec_raises(self, engine):
        bad = pd.DataFrame({"ra": [1.0], "y": [10.0]})
        with pytest.raises(ValueError, match="dec"):
            run(engine.crossmatch(CAT1, bad, RADIUS))

    def test_empty_table_raises(self, engine):
        empty = pd.DataFrame({"ra": [], "dec": []})
        with pytest.raises(ValueError, match="empty"):
            run(engine.crossmatch(empty, CAT2, RADIUS))

    def test_invalid_join_raises(self, engine):
        with pytest.raises(ValueError, match="join"):
            run(engine.crossmatch(CAT1, CAT2, RADIUS, join="bad"))

    def test_invalid_find_raises(self, engine):
        with pytest.raises(ValueError, match="find"):
            run(engine.crossmatch(CAT1, CAT2, RADIUS, find="bad"))

    def test_zero_radius_matches_nothing(self, engine):
        result = run(
            engine.crossmatch(CAT1, CAT2, radius_arcsec=0.0, join="1and2", find="best")
        )
        assert len(result) == 0

    def test_large_radius_matches_all(self, engine):
        result = run(
            engine.crossmatch(
                CAT1, CAT2, radius_arcsec=200_000, join="1and2", find="best"
            )
        )
        # With best match, at most len(CAT1) rows
        assert len(result) == len(CAT1)


class TestMultiCrossmatch:
    """Test multi_crossmatch chaining."""

    def test_three_table_match(self, engine):
        # Third table overlaps with A and B positions
        cat3 = pd.DataFrame(
            {
                "ra": [10.00005, 20.00005],
                "dec": [10.0, 10.0],
                "id3": ["X", "Y"],
            }
        )
        result = run(engine.multi_crossmatch([CAT1, CAT2, cat3], RADIUS))
        assert len(result) == 2
        assert set(result["id1"]) == {"A", "B"}

    def test_two_tables_same_as_crossmatch(self, engine):
        result_multi = run(engine.multi_crossmatch([CAT1, CAT2], RADIUS))
        result_single = run(
            engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", find="best")
        )
        assert len(result_multi) == len(result_single)

    def test_single_table_raises(self, engine):
        with pytest.raises(ValueError, match="at least 2"):
            run(engine.multi_crossmatch([CAT1], RADIUS))


class TestSeparationAccuracy:
    """Verify computed separations match independent calculation."""

    def test_known_separation(self, engine):
        t1 = pd.DataFrame({"ra": [180.0], "dec": [0.0], "label": ["origin"]})
        # 1 arcsec offset in RA at equator
        offset_ra = 1.0 / 3600.0
        t2 = pd.DataFrame(
            {"ra": [180.0 + offset_ra], "dec": [0.0], "label": ["offset"]}
        )
        result = run(engine.crossmatch(t1, t2, 5.0, join="1and2", find="best"))
        assert len(result) == 1
        assert abs(result["separation_arcsec"].iloc[0] - 1.0) < 0.01

    def test_high_dec_cos_correction(self, engine):
        """RA separation shrinks at high declination due to cos(dec)."""
        t1 = pd.DataFrame({"ra": [180.0], "dec": [60.0]})
        # 2 arcsec in RA at equator = 1 arcsec at dec=60
        offset_ra = 2.0 / 3600.0
        t2 = pd.DataFrame({"ra": [180.0 + offset_ra], "dec": [60.0]})
        result = run(engine.crossmatch(t1, t2, 5.0, join="1and2", find="best"))
        sep = result["separation_arcsec"].iloc[0]
        # At dec=60, the actual separation should be ~1 arcsec
        assert abs(sep - 1.0) < 0.05


class TestSTILTSAvailability:
    """Verify STILTS availability detection (always False in test env)."""

    def test_stilts_not_available_by_default(self, engine):
        assert engine.stilts_available is False

    def test_falls_back_to_astropy(self, engine):
        """Even with use_stilts=True, should fall back and succeed."""
        result = run(
            engine.crossmatch(CAT1, CAT2, RADIUS, join="1and2", use_stilts=True)
        )
        assert len(result) == 2
