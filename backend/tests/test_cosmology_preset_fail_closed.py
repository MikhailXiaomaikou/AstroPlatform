from __future__ import annotations

import pytest


def test_unknown_cosmology_preset_raises_instead_of_silent_planck_fallback():
    from app.services.cosmology import get_preset

    with pytest.raises(ValueError, match="unknown cosmology preset"):
        get_preset("plank18_typo")
