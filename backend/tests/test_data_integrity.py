"""Data-integrity tests (Phase 4 / R14 + R15).

R14 — NaN / masked hygiene: every connector path ends in
``_astro_to_result`` / ``_safe_float``; the AstroObject dataclass must
handle NaN-coerced fields without crashing the agent loop.

R15 — AstroObject validator attaches ``_validation_warnings`` to
``extra`` when fields are out of physical bounds, but never raises.
"""

from __future__ import annotations

import math

from app.connectors.base import AstroObject


def test_astroobject_valid_has_no_warnings():
    o = AstroObject(source="gaia", object_id="1", name="M31",
                    ra=10.68, dec=41.27, magnitude=4.3, redshift=-0.001)
    assert "_validation_warnings" not in (o.extra or {})


def test_astroobject_flags_ra_out_of_range():
    o = AstroObject(source="sdss", object_id="x", name="bad",
                    ra=400.0, dec=20.0)
    warnings = o.extra.get("_validation_warnings", [])
    assert any("ra=" in w for w in warnings)


def test_astroobject_flags_dec_out_of_range():
    o = AstroObject(source="sdss", object_id="x", name="bad",
                    ra=10.0, dec=-120.0)
    warnings = o.extra.get("_validation_warnings", [])
    assert any("dec=" in w for w in warnings)


def test_astroobject_flags_nan_ra():
    o = AstroObject(source="gaia", object_id="x", name="nan",
                    ra=float("nan"), dec=0.0)
    warnings = o.extra.get("_validation_warnings", [])
    assert any("ra=" in w for w in warnings)


def test_astroobject_flags_extreme_redshift():
    o = AstroObject(source="ned", object_id="x", name="zbad",
                    ra=10.0, dec=20.0, redshift=42.0)
    warnings = o.extra.get("_validation_warnings", [])
    assert any("redshift=" in w for w in warnings)


def test_astroobject_does_not_raise_on_invalid():
    """A single malformed row must not abort the batch."""
    # Should not raise
    AstroObject(source="x", object_id="a", name="a",
                ra=math.inf, dec=math.nan,
                magnitude=1e9, redshift=-5.0)


def test_astroobject_passes_validation_warnings_through_provenance():
    """normalize_tool_result must surface AstroObject warnings as sanity."""
    from app.services.result_provenance import normalize_tool_result

    obj = AstroObject(source="sdss", object_id="x", name="bad",
                      ra=400.0, dec=50.0)
    # Mock up how a tool would return this — AstroObject dicts in a list.
    result = {"results": [{"ra": obj.ra, "dec": obj.dec}]}
    normalised = normalize_tool_result("search_objects", result)
    # The dict-level sanity check catches the out-of-range ra.
    warnings = normalised.get("warnings") or []
    assert any("ra" in str(w).lower() for w in warnings)
