"""Central registry of archive / catalog release strings (Phase 5 / R17).

One source of truth for version strings so the AI system prompt, the
tool result envelopes, and any user-visible citation use the same
values.  If a data release changes (e.g. Gaia DR3 → DR4), update this
file and redeploy; the system prompt picks up the change at runtime.
"""

from __future__ import annotations

ARCHIVE_VERSIONS: dict[str, str] = {
    "gaia": "DR3",
    "sdss": "DR18",
    "simbad": "live-TAP",
    "ned": "live-2024",
    "vizier": "live",
    "mast": "live",
    "twomass": "II/246",
    "allwise": "II/328",
    "chandra": "CSC 2.1",
    "xmm": "4XMM-DR13",
    "panstarrs": "DR2",
    "ztf": "DR21",
    "lamost": "DR9",
    "desi": "DR1",
    "alma": "live",
    "eso": "live",
    "irsa": "live",
    "jwst": "live",
    "nvss": "1998",
    "first": "14dec17",
    "jpl_horizons": "live",
    "atnf_pulsar": "PSRCAT 2.6.1",
    "sparc": "2016",
    "frbstats": "live",
    "parsec_isochrones": "CMD 3.9",
}


def archive_version(source: str) -> str | None:
    """Look up the release string for a connector / source name."""
    return ARCHIVE_VERSIONS.get(str(source or "").lower())


def archive_manifest_text() -> str:
    """Rendered one-line summary used in the system prompt."""
    items = [f"{k}={v}" for k, v in sorted(ARCHIVE_VERSIONS.items())]
    return ", ".join(items)
