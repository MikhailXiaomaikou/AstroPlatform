"""Single source of truth for cosmology presets (Phase D2 + PART AA).

Every function that needs H0, Ωm, Ωb, σ8, ns, or a luminosity distance
reads through this module so a paper produced on the platform never
silently mixes values across releases.

Default: ``planck18`` (Planck Collaboration VI 2020, A&A 641 A6).
Callers can override per-call with ``cosmology="<preset>"`` kwarg or set
the ``ASTRO_COSMOLOGY`` env var to one of the named presets below.

All parameter values + bibcodes are taken verbatim from the cited
peer-reviewed paper. When you cite a cosmology in a chat reply, ALWAYS
use the bibcode from this table — the citation validator looks it up
in the tool_results universe.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# PART AA: 4 named presets with complete metadata + bibcodes.
# Each entry is the source of truth for ONE published cosmology choice.
#
# Numbers come from the cited paper verbatim. Where a preset doesn't
# constrain a parameter directly (e.g. SH0ES does not measure Om0),
# the field is None — not silently filled with someone else's value.
# ---------------------------------------------------------------------

PRESETS: dict[str, dict[str, Any]] = {
    "planck18": {
        "name": "planck18",
        "label": "Planck 2018 baseline (TT,TE,EE+lowE+lensing)",
        # Planck Collab VI 2020 A&A 641 A6, Table 2 col. "TT,TE,EE+lowE+lensing"
        "H0": 67.36,
        "H0_err": 0.54,
        "Om0": 0.3153,
        "Om0_err": 0.0073,
        "Ob0": 0.04930,
        "Ob0_err": 0.00026,
        "sigma8": 0.8111,
        "sigma8_err": 0.0060,
        "ns": 0.9649,
        "ns_err": 0.0042,
        "Tcmb0": 2.7255,  # Fixsen 2009 ApJ 707 916
        "Tcmb0_err": 0.0006,
        "w0": -1.0,
        "wa": 0.0,
        "bibcode": "2020A&A...641A...6P",
        "doi": "10.1051/0004-6361/201833910",
        "tcmb_bibcode": "2009ApJ...707..916F",
        # NOT astropy's Planck18 built-in: that object is the +BAO best fit
        # (H0=67.66, Om0=0.30966), but this preset is the CMB-only column
        # (H0=67.36, Om0=0.3153). Aliasing to the built-in silently computed
        # distances at +BAO values while citing CMB-only numbers — build from
        # our own values so computed distances match the cited cosmology.
        "astropy_alias": None,
        "reference": "Planck Collaboration VI 2020, A&A 641 A6 (CMB only)",
        "description": (
            "CMB-only LambdaCDM baseline. Standard for high-z / CMB / "
            "Euclid / LSST work. Has ~5σ tension with local SH0ES H0."
        ),
    },
    "planck18_bao": {
        "name": "planck18_bao",
        "label": "Planck 2018 + BAO (TT,TE,EE+lowE+lensing+BAO)",
        # Planck Collab VI 2020 A&A 641 A6, Table 2 col. "+BAO"
        "H0": 67.66,
        "H0_err": 0.42,
        "Om0": 0.3111,
        "Om0_err": 0.0056,
        "Ob0": 0.04897,
        "Ob0_err": 0.00031,
        "sigma8": 0.8102,
        "sigma8_err": 0.0060,
        "ns": 0.9665,
        "ns_err": 0.0038,
        "Tcmb0": 2.7255,
        "Tcmb0_err": 0.0006,
        "w0": -1.0,
        "wa": 0.0,
        "bibcode": "2020A&A...641A...6P",
        "doi": "10.1051/0004-6361/201833910",
        "tcmb_bibcode": "2009ApJ...707..916F",
        "astropy_alias": None,  # not a built-in astropy preset
        "reference": "Planck Collaboration VI 2020, A&A 641 A6 (+BAO)",
        "description": (
            "Planck 2018 CMB + Baryon Acoustic Oscillation. Tighter than "
            "CMB-only, slightly higher H0, narrower Om0. Use when one "
            "late-Universe distance scale tied to BAO is desired."
        ),
    },
    "freedman21_trgb": {
        "name": "freedman21_trgb",
        "label": "Freedman 2021 TRGB (Tip of the Red Giant Branch)",
        # Freedman 2021 ApJ 919 16
        "H0": 69.8,
        "H0_err": 1.7,
        # TRGB does not directly constrain Om0/Ob0/σ8/ns; record None so
        # callers know not to quote them as if Freedman 2021 measured them.
        "Om0": None,
        "Om0_err": None,
        "Ob0": None,
        "Ob0_err": None,
        "sigma8": None,
        "sigma8_err": None,
        "ns": None,
        "ns_err": None,
        "Tcmb0": 2.7255,
        "Tcmb0_err": 0.0006,
        "w0": -1.0,
        "wa": 0.0,
        "bibcode": "2021ApJ...919...16F",
        "doi": "10.3847/1538-4357/ac0e95",
        "tcmb_bibcode": "2009ApJ...707..916F",
        "astropy_alias": None,
        "reference": "Freedman 2021, ApJ 919, 16 (TRGB-only)",
        "description": (
            "Independent local distance ladder via Tip of the Red Giant "
            "Branch (no Cepheids). Sits between Planck CMB and SH0ES; "
            "useful as a tension-neutral pivot. H0 only — Om0/sigma8/ns "
            "not measured by this work."
        ),
    },
    "riess22_shoes": {
        "name": "riess22_shoes",
        "label": "Riess 2022 SH0ES (Cepheid + SN Ia distance ladder)",
        # Riess et al. 2022 ApJL 934 L7
        "H0": 73.04,
        "H0_err": 1.04,
        # SH0ES measures H0 conditional on a fixed background cosmology;
        # Om0/Ob0/σ8/ns are NOT constrained by this work. Record None.
        "Om0": None,
        "Om0_err": None,
        "Ob0": None,
        "Ob0_err": None,
        "sigma8": None,
        "sigma8_err": None,
        "ns": None,
        "ns_err": None,
        "Tcmb0": 2.7255,
        "Tcmb0_err": 0.0006,
        "w0": -1.0,
        "wa": 0.0,
        "bibcode": "2022ApJ...934L...7R",
        "doi": "10.3847/2041-8213/ac5c5b",
        "tcmb_bibcode": "2009ApJ...707..916F",
        "astropy_alias": None,
        "reference": "Riess et al. 2022, ApJL 934, L7 (SH0ES Cepheid+SN Ia)",
        "description": (
            "Local distance ladder (Cepheid + Type Ia SN). High end of "
            "the Hubble tension; ~5σ above Planck CMB. Use when working "
            "with low-z standard candles and want to match Riess 2022 "
            "systematics. H0 only — other cosmological params not "
            "measured by this work."
        ),
    },
}

DEFAULT_PRESET = "planck18"


def list_presets() -> list[str]:
    """Return the canonical preset names in stable alphabetical order."""
    return sorted(PRESETS.keys())


def default_cosmology_name() -> str:
    """The platform-wide default preset name.

    Reads ``ASTRO_COSMOLOGY`` env var; if unset or invalid, returns
    ``planck18``. Backwards compat: also accepts the old astropy-style
    name ``"Planck18"``.
    """
    raw = (os.getenv("ASTRO_COSMOLOGY") or "").strip()
    if not raw:
        return DEFAULT_PRESET
    if raw in PRESETS:
        return raw
    if raw == "Planck18":
        return "planck18"
    logger.warning(
        "ASTRO_COSMOLOGY=%r not a known preset; using %s. Available: %s",
        raw, DEFAULT_PRESET, list_presets(),
    )
    return DEFAULT_PRESET


def get_preset(name: str | None = None) -> dict[str, Any]:
    """Return the full metadata dict for a named preset.

    Backwards-compat: also accepts the legacy astropy aliases
    ``Planck18`` / ``Planck15`` / ``WMAP9`` / ``WMAP7`` / ``WMAP5`` and
    the ``FlatLambdaCDM_H<H0>_Om<Om>`` spec — those round-trip through
    a synthetic preset entry so callers that branch on `preset["bibcode"]`
    still work, but the synthetic entries explicitly carry
    ``bibcode=None`` so the citation validator never auto-attributes
    them to a real paper.
    """
    key = (name or default_cosmology_name() or DEFAULT_PRESET).strip()
    if key in PRESETS:
        return dict(PRESETS[key])

    # Legacy astropy alias -> preset.
    if key in {"Planck18"}:
        return dict(PRESETS["planck18"])

    # Legacy compat shims for non-PART-AA cosmologies still referenced
    # by callers; these have no bibcode in our table.
    if key in {"Planck15", "WMAP9", "WMAP7", "WMAP5"} or key.startswith(
        "FlatLambdaCDM"
    ):
        return {
            "name": key,
            "label": key,
            "H0": None,
            "Om0": None,
            "Ob0": None,
            "sigma8": None,
            "ns": None,
            "Tcmb0": 2.7255,
            "w0": -1.0,
            "wa": 0.0,
            "bibcode": None,
            "doi": None,
            "tcmb_bibcode": "2009ApJ...707..916F",
            "astropy_alias": key,
            "reference": "Astropy legacy alias (no platform preset)",
            "description": (
                f"{key} resolves through astropy.cosmology directly; "
                "this is a back-compat path, not a curated PART AA preset. "
                "When citing, use a PART AA preset (planck18 / "
                "planck18_bao / freedman21_trgb / riess22_shoes)."
            ),
        }

    logger.warning(
        "get_preset(%r): unknown preset; falling back to %s. Available: %s",
        name, DEFAULT_PRESET, list_presets(),
    )
    return dict(PRESETS[DEFAULT_PRESET])


def build_cosmology_from_preset(name: str | None = None):
    """Build an astropy ``FlatLambdaCDM`` from a named preset.

    Builds a fresh ``FlatLambdaCDM`` from the preset's own H0/Om0/Ob0/Tcmb0
    so computed distances match the cited values. No PART AA preset aliases
    to an astropy built-in (planck18 deliberately does not — astropy's
    Planck18 is the +BAO fit, not our CMB-only column). The built-in path
    below only fires for legacy capital-case aliases. For TRGB / SH0ES
    (where Om0 is None), uses the Planck18 Om0 as the background-cosmology
    working assumption — documented in the result via
    :func:`cosmology_manifest`.
    """
    from astropy.cosmology import FlatLambdaCDM, Planck15, Planck18, WMAP5, WMAP7, WMAP9

    preset = get_preset(name)
    alias = preset.get("astropy_alias")
    builtins = {
        "Planck18": Planck18,
        "Planck15": Planck15,
        "WMAP9": WMAP9,
        "WMAP7": WMAP7,
        "WMAP5": WMAP5,
    }
    if alias and alias in builtins:
        return builtins[alias]

    # Legacy FlatLambdaCDM_H..._Om... spec round-trip via the existing
    # parser in get_cosmology() below.
    if preset["name"].startswith("FlatLambdaCDM"):
        return get_cosmology(preset["name"])

    # PART AA presets that aren't astropy built-ins (planck18_bao /
    # freedman21_trgb / riess22_shoes). For TRGB and SH0ES, Om0 is None
    # — use Planck18 Om0 as the assumed background.
    h0 = preset["H0"] if preset["H0"] is not None else PRESETS["planck18"]["H0"]
    om0 = preset["Om0"] if preset["Om0"] is not None else PRESETS["planck18"]["Om0"]
    ob0 = preset["Ob0"] if preset["Ob0"] is not None else PRESETS["planck18"]["Ob0"]
    tcmb0 = preset["Tcmb0"] or 2.7255

    return FlatLambdaCDM(H0=h0, Om0=om0, Ob0=ob0, Tcmb0=tcmb0, name=preset["name"])


def get_cosmology(name: str | None = None):
    """Return an astropy Cosmology instance by name.

    Supports:
      - PART AA presets (``planck18`` / ``planck18_bao`` /
        ``freedman21_trgb`` / ``riess22_shoes``).
      - Legacy astropy aliases (``Planck18``, ``Planck15``, ``WMAP9``,
        ``WMAP7``, ``WMAP5``).
      - ``FlatLambdaCDM_H<H0>_Om<Om>`` spec, e.g.
        ``FlatLambdaCDM_H73p8_Om0p295``.

    Unknown names fall back to the default preset (planck18) with a
    warning so the caller still gets a valid cosmology.
    """
    from astropy.cosmology import FlatLambdaCDM, Planck15, WMAP5, WMAP7, WMAP9

    key = (name or default_cosmology_name()).strip()

    # Legacy capital-case astropy name for our curated CMB-only preset:
    # route to the PART AA preset so computed distances match the cited
    # 67.36/0.3153. astropy's built-in Planck18 is the +BAO fit (67.66).
    if key == "Planck18":
        key = "planck18"

    # PART AA preset path
    if key in PRESETS:
        return build_cosmology_from_preset(key)

    # Legacy astropy aliases (Planck18 handled above via the curated preset)
    table = {
        "Planck15": Planck15,
        "WMAP9": WMAP9,
        "WMAP7": WMAP7,
        "WMAP5": WMAP5,
    }
    if key in table:
        return table[key]

    # Legacy spec parser: FlatLambdaCDM_H73p8_Om0p295
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

    logger.warning("Unknown cosmology %r; falling back to planck18 preset", key)
    return build_cosmology_from_preset("planck18")


def cosmology_manifest(name: str | None = None) -> dict[str, Any]:
    """Provenance manifest for the system prompt and result envelopes.

    Always includes the preset name + bibcode + DOI + label so the
    citation validator + paper generator can attribute each
    cosmology-derived number to its source paper.
    """
    preset = get_preset(name)
    return {
        "name": preset["name"],
        "label": preset["label"],
        "H0_km_s_Mpc": preset["H0"],
        "H0_err": preset.get("H0_err"),
        "Om0": preset["Om0"],
        "Om0_err": preset.get("Om0_err"),
        "Ob0": preset["Ob0"],
        "Ob0_err": preset.get("Ob0_err"),
        "sigma8": preset["sigma8"],
        "ns": preset["ns"],
        "Tcmb0_K": preset["Tcmb0"],
        "w0": preset["w0"],
        "wa": preset["wa"],
        "bibcode": preset["bibcode"],
        "doi": preset["doi"],
        "tcmb_bibcode": preset["tcmb_bibcode"],
        "reference": preset["reference"],
    }
