"""Single source of truth for cosmology (Phase D2).

Every function that needs H0, Ω_m, or a luminosity-distance reads through
this module so a paper from the platform never silently mixes values.

Default: Planck18 (arXiv:1807.06209).  Callers can override per-call or
set the ``ASTRO_COSMOLOGY`` env var to one of the named entries below.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def default_cosmology_name() -> str:
    return os.getenv("ASTRO_COSMOLOGY", "Planck18").strip() or "Planck18"


def get_cosmology(name: str | None = None):
    """Return an astropy Cosmology instance by name.

    Supported: ``Planck18`` (default), ``Planck15``, ``WMAP9``,
    ``WMAP7``, ``WMAP5``, ``FlatLambdaCDM_H67p4_Om0p315``.  Unknown
    names fall back to Planck18 with a warning so the caller still
    gets a valid cosmology.
    """
    from astropy.cosmology import (
        FlatLambdaCDM,
        Planck15,
        Planck18,
        WMAP5,
        WMAP7,
        WMAP9,
    )

    key = (name or default_cosmology_name()).strip()
    table = {
        "Planck18": Planck18,
        "Planck15": Planck15,
        "WMAP9": WMAP9,
        "WMAP7": WMAP7,
        "WMAP5": WMAP5,
    }
    if key in table:
        return table[key]
    if key.startswith("FlatLambdaCDM"):
        parts = key.split("_")
        h0 = 67.4
        om0 = 0.315
        for p in parts[1:]:
            if p.startswith("H"):
                try:
                    h0 = float(p[1:].replace("p", "."))
                except ValueError:
                    pass
            elif p.startswith("Om"):
                try:
                    om0 = float(p[2:].replace("p", "."))
                except ValueError:
                    pass
        return FlatLambdaCDM(H0=h0, Om0=om0)
    logger.warning("Unknown cosmology %r; falling back to Planck18", key)
    return Planck18


def cosmology_manifest() -> dict[str, str | float]:
    """One-line provenance for the system prompt and result envelopes."""
    cosmo = get_cosmology()
    return {
        "name": default_cosmology_name(),
        "H0_km_s_Mpc": float(cosmo.H0.value),
        "Om0": float(cosmo.Om0),
        "Ob0": float(getattr(cosmo, "Ob0", 0.0) or 0.0),
        "reference": "Planck Collaboration VI 2018 (arXiv:1807.06209)",
    }
